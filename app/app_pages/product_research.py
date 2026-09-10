from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from app.database import session_scope
from app.repositories import list_products
from app.research_catalog.quality import assess_product_quality
from app.research_catalog.repositories import (
    archive_research_product,
    import_legacy_product,
    list_research_genres,
    list_research_products,
    save_evidence,
    save_offer,
    save_research_product,
)
from app.research_catalog.schemas import (
    EvidenceInput,
    OfferInput,
    PriceTaxStatus,
    ProductCandidateInput,
    ProductResearchStatus,
    SourceType,
    VerificationStatus,
)
from app.research_catalog.time import JST, format_jst, to_jst

PRODUCT_STATUS_LABELS = {
    "candidate": "候補",
    "researching": "調査中",
    "comparable": "比較可能",
    "on_hold": "保留",
    "archived": "アーカイブ",
}
SOURCE_TYPE_LABELS = {
    "manufacturer": "メーカー公式",
    "retailer": "販売店",
    "third_party": "第三者情報",
    "own_measurement": "自分の実測",
}
VERIFICATION_LABELS = {
    "confirmed_fact": "確認済みの事実",
    "hypothesis": "仮説",
    "unverified": "未確認",
}
TAX_LABELS = {
    "unknown": "不明",
    "tax_included": "税込",
    "tax_excluded": "税抜",
}


st.caption(
    "国内向けの商品候補、複数の販売先、調査根拠、実測を管理します。"
    "日時はUTCで保存し、画面では日本時間（JST）で表示します。"
)

with session_scope() as session:
    all_products = list_research_products(session, include_archived=True)
    genres = list_research_genres(session)
    legacy_products = list_products(session)

with st.container(horizontal=True):
    genre_filter = st.selectbox("ジャンル", ["すべて", *genres], key="research_genre_filter")
    status_filter = st.selectbox(
        "状態",
        ["アーカイブ以外", *PRODUCT_STATUS_LABELS],
        format_func=lambda value: PRODUCT_STATUS_LABELS.get(value, value),
        key="research_status_filter",
    )
    audience_filter = st.text_input(
        "想定読者・悩み",
        placeholder="例：狭い机を整理したい人",
        key="research_audience_filter",
    )
    freshness_days = int(
        st.number_input(
            "条件の再確認期限（日）",
            min_value=1,
            max_value=365,
            value=30,
            key="research_freshness_days",
            help="代表販売先の価格・送料・報酬条件を再確認する目安です。",
        )
    )
    review_only = st.checkbox(
        "要確認だけ",
        key="research_review_only",
        help="未登録・未確認・期限切れの項目がある商品に絞ります。",
    )

quality_reports = {
    product.id: assess_product_quality(product, freshness_days=freshness_days)
    for product in all_products
}

filtered_products = [
    product
    for product in all_products
    if (genre_filter == "すべて" or product.genre == genre_filter)
    and (
        (status_filter == "アーカイブ以外" and product.status != "archived")
        or product.status == status_filter
    )
    and (
        not audience_filter.strip()
        or audience_filter.strip().lower()
        in f"{product.audience} {product.pain_point}".lower()
    )
    and (not review_only or quality_reports[product.id].needs_review)
]

active_reports = [
    quality_reports[product.id] for product in all_products if product.status != "archived"
]
with st.container(horizontal=True):
    st.metric("登録商品", len(active_reports), border=True)
    st.metric(
        "要確認",
        sum(bool(report.blocking_issues) for report in active_reports),
        border=True,
    )
    st.metric(
        "注意あり",
        sum(report.needs_review and not report.blocking_issues for report in active_reports),
        border=True,
    )
    st.metric(
        "確認済み",
        sum(not report.needs_review for report in active_reports),
        border=True,
    )

if filtered_products:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "内部ID": product.id,
                    "商品ID": product.product_code,
                    "商品名": product.product_name,
                    "メーカー": product.manufacturer or "未確認",
                    "型番・サイズ": " / ".join(
                        value for value in [product.model_number, product.variant] if value
                    )
                    or "未確認",
                    "ジャンル": product.genre or "未設定",
                    "状態": PRODUCT_STATUS_LABELS.get(product.status, product.status),
                    "想定読者": product.audience or "未設定",
                    "販売先": len(product.offers),
                    "調査記録": len(product.evidence),
                    "確認状態": quality_reports[product.id].label,
                    "最終条件確認": (
                        to_jst(quality_reports[product.id].latest_terms_checked_at)
                        if quality_reports[product.id].latest_terms_checked_at
                        else None
                    ),
                    "不足・期限切れ": "／".join(
                        issue.message for issue in quality_reports[product.id].issues
                    )
                    or "なし",
                    "更新日時": to_jst(product.updated_at),
                }
                for product in filtered_products
            ]
        ),
        hide_index=True,
        column_config={
            "内部ID": None,
            "商品名": st.column_config.TextColumn(pinned=True, width="large"),
            "更新日時": st.column_config.DatetimeColumn(format="YYYY/MM/DD HH:mm"),
            "最終条件確認": st.column_config.DatetimeColumn(format="YYYY/MM/DD HH:mm"),
        },
    )
else:
    st.info(
        "条件に合う商品候補はありません。下の「新規登録」を選び、商品IDと商品名から登録してください。",
        icon=":material/inventory_2:",
    )

st.subheader("商品候補の登録・編集", anchor=False)
product_options = [0, *[product.id for product in all_products]]
product_selection_key = "research_product_editor_selection"
next_product_selection = st.session_state.pop(
    "research_product_editor_selection_next", None
)
if next_product_selection in product_options:
    st.session_state[product_selection_key] = next_product_selection
selected_product_id = int(
    st.selectbox(
        "編集対象",
        product_options,
        format_func=lambda value: (
            "新規登録"
            if value == 0
            else next(
                f"{product.product_code}｜{product.product_name}"
                for product in all_products
                if product.id == value
            )
        ),
        key=product_selection_key,
    )
    or 0
)
current_product = next(
    (product for product in all_products if product.id == selected_product_id), None
)
editor_key = str(selected_product_id or "new")

if current_product:
    current_quality = quality_reports[current_product.id]
    if current_quality.blocking_issues:
        st.error(
            "制作前に確認が必要です: "
            + "／".join(issue.message for issue in current_quality.blocking_issues),
            icon=":material/fact_check:",
        )
    elif current_quality.issues:
        st.warning(
            "確認推奨: " + "／".join(issue.message for issue in current_quality.issues),
            icon=":material/update:",
        )
    else:
        st.success("販売条件と確認済み根拠は、設定した期限内です。")

with st.form(f"research_product_form_{editor_key}"):
    identity_columns = st.columns(2)
    product_code = identity_columns[0].text_input(
        "商品ID",
        value=current_product.product_code if current_product else "",
        help="同一商品の異なる型番・サイズは別の商品IDで登録してください。",
    )
    product_name = identity_columns[1].text_input(
        "商品名", value=current_product.product_name if current_product else ""
    )
    detail_columns = st.columns(3)
    manufacturer = detail_columns[0].text_input(
        "メーカー", value=current_product.manufacturer if current_product else ""
    )
    model_number = detail_columns[1].text_input(
        "型番", value=current_product.model_number if current_product else ""
    )
    variant = detail_columns[2].text_input(
        "サイズ・バリエーション", value=current_product.variant if current_product else ""
    )
    genre = st.text_input(
        "ジャンル（自由入力）",
        value=current_product.genre if current_product else "",
        placeholder="例：コーヒー器具（検証候補）",
    )
    audience = st.text_area(
        "想定読者", value=current_product.audience if current_product else "", height=80
    )
    pain_point = st.text_area(
        "読者の具体的な悩み",
        value=current_product.pain_point if current_product else "",
        height=80,
    )
    use_case = st.text_area(
        "利用場面", value=current_product.use_case if current_product else "", height=80
    )
    status = st.selectbox(
        "状態",
        list(PRODUCT_STATUS_LABELS),
        index=list(PRODUCT_STATUS_LABELS).index(current_product.status)
        if current_product
        else 0,
        format_func=lambda value: PRODUCT_STATUS_LABELS[value],
    )
    linked_id = current_product.legacy_product_id if current_product else None
    legacy_product_id = st.selectbox(
        "既存の制作商品とのリンク（任意）",
        [None, *[product.id for product in legacy_products]],
        index=(
            [None, *[product.id for product in legacy_products]].index(linked_id)
            if linked_id in {product.id for product in legacy_products}
            else 0
        ),
        format_func=lambda value: (
            "リンクしない"
            if value is None
            else next(
                f"{product.item_code}｜{product.item_name}"
                for product in legacy_products
                if product.id == value
            )
        ),
        help="リンクすると、企画から既存の投稿文作成画面へ商品選択を引き継げます。",
    )
    save_product = st.form_submit_button(
        "商品候補を保存", icon=":material/save:", type="primary"
    )

if save_product:
    try:
        payload = ProductCandidateInput(
            product_code=product_code,
            product_name=product_name,
            manufacturer=manufacturer,
            model_number=model_number,
            variant=variant,
            genre=genre,
            audience=audience,
            pain_point=pain_point,
            use_case=use_case,
            status=ProductResearchStatus(status),
            legacy_product_id=legacy_product_id,
        )
        with session_scope() as session:
            saved_product = save_research_product(
                session, payload, product_id=selected_product_id or None
            )
        st.session_state["research_product_editor_selection_next"] = saved_product.id
        st.toast("商品候補を保存しました。")
        st.rerun()
    except (ValidationError, ValueError) as exc:
        st.error(f"商品候補を保存できません：{exc}")

if current_product and current_product.status != "archived":
    if st.button("この商品をアーカイブ", icon=":material/archive:"):
        with session_scope() as session:
            archive_research_product(session, current_product.id)
        st.toast("商品候補をアーカイブしました。削除はしていません。")
        st.rerun()

unlinked_legacy = [
    product
    for product in legacy_products
    if product.id not in {item.legacy_product_id for item in all_products}
]
with st.expander("既存の保存商品から取り込む", icon=":material/download:"):
    if unlinked_legacy:
        import_id = st.selectbox(
            "取り込む商品",
            [product.id for product in unlinked_legacy],
            format_func=lambda value: next(
                product.item_name for product in unlinked_legacy if product.id == value
            ),
        )
        st.caption("既存価格・料率を引き継ぎますが、公開前の再確認が必要です。")
        if st.button("商品候補へ取り込む", icon=":material/add_link:"):
            with session_scope() as session:
                imported = import_legacy_product(session, int(import_id))
            st.session_state["research_product_editor_selection_next"] = imported.id
            st.toast("既存商品を商品候補へ取り込みました。")
            st.rerun()
    else:
        st.caption("取り込み可能な既存商品はありません。")

if current_product is None:
    st.info("商品を保存すると、複数の販売先と調査記録を追加できます。")
    st.stop()
assert current_product is not None

st.subheader("販売先", anchor=False)
offer_options = [0, *[offer.id for offer in current_product.offers]]
offer_labels = {
    0: "新しい販売先",
    **{offer.id: offer.seller_name for offer in current_product.offers},
}
offer_selection_key = f"research_offer_selection_{current_product.id}"
next_offer_selection = st.session_state.pop(
    f"{offer_selection_key}_next", None
)
if next_offer_selection in offer_options:
    st.session_state[offer_selection_key] = next_offer_selection
selected_offer_id = int(
    st.selectbox(
        "販売先の編集対象",
        offer_options,
        format_func=lambda value: offer_labels.get(int(value or 0), "販売先"),
        key=offer_selection_key,
    )
    or 0
)
current_offer = next(
    (offer for offer in current_product.offers if offer.id == selected_offer_id), None
)
offer_key = str(selected_offer_id or "new")
offer_checked_jst = to_jst(current_offer.terms_checked_at) if current_offer else None
with st.form(f"research_offer_form_{current_product.id}_{offer_key}"):
    seller_name = st.text_input(
        "販売店名", value=current_offer.seller_name if current_offer else ""
    )
    product_url = st.text_input(
        "通常の商品URL", value=current_offer.product_url if current_offer else ""
    )
    affiliate_url = st.text_input(
        "アフィリエイトURL",
        value=current_offer.affiliate_url if current_offer else "",
        help="クエリパラメーターを含む入力原文を、並べ替えずに保存します。",
    )
    unknown_columns = st.columns(3)
    price_unknown = unknown_columns[0].checkbox(
        "価格は未確認", value=current_offer.price is None if current_offer else True
    )
    shipping_unknown = unknown_columns[1].checkbox(
        "送料は未確認",
        value=current_offer.shipping_fee is None if current_offer else True,
    )
    commission_unknown = unknown_columns[2].checkbox(
        "報酬率は未確認",
        value=current_offer.commission_rate is None if current_offer else True,
    )
    money_columns = st.columns(3)
    price = money_columns[0].number_input(
        "価格",
        min_value=0.0,
        value=float(current_offer.price or 0) if current_offer else 0.0,
        step=1.0,
        disabled=price_unknown,
    )
    shipping_fee = money_columns[1].number_input(
        "送料",
        min_value=0.0,
        value=float(current_offer.shipping_fee or 0) if current_offer else 0.0,
        step=1.0,
        disabled=shipping_unknown,
    )
    currency = money_columns[2].text_input(
        "通貨", value=current_offer.currency if current_offer else "JPY", max_chars=3
    )
    commission_columns = st.columns(2)
    commission_rate = commission_columns[0].number_input(
        "報酬率（%）",
        min_value=0.0,
        max_value=100.0,
        value=float(current_offer.commission_rate or 0) if current_offer else 0.0,
        step=0.1,
        disabled=commission_unknown,
    )
    cap_unknown = commission_columns[1].checkbox(
        "報酬上限は未確認／上限なしを未確認",
        value=current_offer.commission_cap is None if current_offer else True,
    )
    commission_cap = st.number_input(
        "報酬上限額",
        min_value=0.0,
        value=float(current_offer.commission_cap or 0) if current_offer else 0.0,
        step=1.0,
        disabled=cap_unknown,
    )
    price_tax_status = st.selectbox(
        "税込・税抜",
        list(TAX_LABELS),
        index=list(TAX_LABELS).index(current_offer.price_tax_status)
        if current_offer
        else 0,
        format_func=lambda value: TAX_LABELS[value],
    )
    commission_conditions = st.text_area(
        "報酬の上限・適用条件メモ",
        value=current_offer.commission_conditions if current_offer else "",
        height=80,
    )
    commission_base_notes = st.text_area(
        "報酬対象額・税送料の扱い",
        value=current_offer.commission_base_notes if current_offer else "",
        placeholder="不明な場合は「不明」と記録してください。",
        height=80,
    )
    checked_unknown = st.checkbox(
        "価格・報酬条件の確認日時は未確認", value=offer_checked_jst is None
    )
    checked_columns = st.columns(2)
    checked_date = checked_columns[0].date_input(
        "確認日", value=(offer_checked_jst or datetime.now(JST)).date(), disabled=checked_unknown
    )
    checked_time = checked_columns[1].time_input(
        "確認時刻",
        value=(offer_checked_jst or datetime.now(JST)).time().replace(microsecond=0),
        disabled=checked_unknown,
    )
    is_primary = st.checkbox(
        "この販売先を代表表示にする", value=current_offer.is_primary if current_offer else False
    )
    save_offer_button = st.form_submit_button(
        "販売先を保存", icon=":material/store:", type="primary"
    )

if save_offer_button:
    try:
        checked_at = (
            None
            if checked_unknown
            else datetime.combine(checked_date, checked_time, tzinfo=JST)
        )
        offer_payload = OfferInput(
            seller_name=seller_name,
            product_url=product_url,
            affiliate_url=affiliate_url,
            price=None if price_unknown else Decimal(str(price)),
            shipping_fee=None if shipping_unknown else Decimal(str(shipping_fee)),
            currency=currency.upper(),
            price_tax_status=PriceTaxStatus(price_tax_status),
            commission_rate=None if commission_unknown else float(commission_rate),
            commission_cap=None if cap_unknown else Decimal(str(commission_cap)),
            commission_conditions=commission_conditions,
            commission_base_notes=commission_base_notes,
            terms_checked_at=checked_at,
            is_primary=is_primary,
        )
        with session_scope() as session:
            saved_offer = save_offer(
                session,
                current_product.id,
                offer_payload,
                offer_id=selected_offer_id or None,
            )
        st.session_state[f"{offer_selection_key}_next"] = saved_offer.id
        st.toast("販売先を保存しました。")
        st.rerun()
    except (ValidationError, ValueError) as exc:
        st.error(f"販売先を保存できません：{exc}")

if current_product.offers:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "販売店": offer.seller_name,
                    "価格": "未確認" if offer.price is None else f"{offer.price} {offer.currency}",
                    "送料": (
                        "未確認"
                        if offer.shipping_fee is None
                        else f"{offer.shipping_fee} {offer.currency}"
                    ),
                    "報酬率": (
                        "未確認" if offer.commission_rate is None else f"{offer.commission_rate}%"
                    ),
                    "税区分": TAX_LABELS.get(offer.price_tax_status, offer.price_tax_status),
                    "確認日時": format_jst(offer.terms_checked_at),
                    "商品URL": offer.product_url,
                    "アフィリエイトURL": offer.affiliate_url,
                }
                for offer in current_product.offers
            ]
        ),
        hide_index=True,
        column_config={
            "商品URL": st.column_config.LinkColumn(),
            "アフィリエイトURL": st.column_config.LinkColumn(),
        },
    )

st.subheader("調査記録・実測", anchor=False)
evidence_options = [0, *[item.id for item in current_product.evidence]]
evidence_labels = {
    0: "新しい調査記録",
    **{item.id: item.summary[:60] for item in current_product.evidence},
}
evidence_selection_key = f"research_evidence_selection_{current_product.id}"
next_evidence_selection = st.session_state.pop(
    f"{evidence_selection_key}_next", None
)
if next_evidence_selection in evidence_options:
    st.session_state[evidence_selection_key] = next_evidence_selection
selected_evidence_id = int(
    st.selectbox(
        "調査記録の編集対象",
        evidence_options,
        format_func=lambda value: evidence_labels.get(int(value or 0), "調査記録"),
        key=evidence_selection_key,
    )
    or 0
)
current_evidence = next(
    (item for item in current_product.evidence if item.id == selected_evidence_id), None
)
evidence_checked_jst = (
    to_jst(current_evidence.checked_at) if current_evidence else datetime.now(JST)
) or datetime.now(JST)
evidence_key = str(selected_evidence_id or "new")
with st.form(f"research_evidence_form_{current_product.id}_{evidence_key}"):
    summary = st.text_area(
        "確認した内容",
        value=current_evidence.summary if current_evidence else "",
        height=100,
    )
    source_columns = st.columns(2)
    source_type = source_columns[0].selectbox(
        "出典の種類",
        list(SOURCE_TYPE_LABELS),
        index=list(SOURCE_TYPE_LABELS).index(current_evidence.source_type)
        if current_evidence
        else 0,
        format_func=lambda value: SOURCE_TYPE_LABELS[value],
    )
    verification_status = source_columns[1].selectbox(
        "情報の状態",
        list(VERIFICATION_LABELS),
        index=list(VERIFICATION_LABELS).index(current_evidence.verification_status)
        if current_evidence
        else 2,
        format_func=lambda value: VERIFICATION_LABELS[value],
    )
    source_url = st.text_input(
        "出典URL",
        value=current_evidence.source_url if current_evidence else "",
        help="自分の実測の場合は空欄で保存できます。URLを入れただけでは確認済みになりません。",
    )
    evidence_date_columns = st.columns(2)
    evidence_date = evidence_date_columns[0].date_input(
        "確認日", value=evidence_checked_jst.date()
    )
    evidence_time = evidence_date_columns[1].time_input(
        "確認時刻", value=evidence_checked_jst.time().replace(microsecond=0)
    )
    actually_used = st.checkbox(
        "実際に使用した",
        value=current_evidence.actually_used if current_evidence else False,
    )
    use_conditions = st.text_area(
        "使用条件",
        value=current_evidence.use_conditions if current_evidence else "",
        placeholder="例：室温22℃、1週間、毎朝1回",
        height=80,
    )
    observations = st.text_area(
        "観察内容",
        value=current_evidence.observations if current_evidence else "",
        placeholder="実使用した場合は、メーカーの訴求と分けて事実を記録してください。",
        height=100,
    )
    save_evidence_button = st.form_submit_button(
        "調査記録を保存", icon=":material/fact_check:", type="primary"
    )

if save_evidence_button:
    try:
        evidence_payload = EvidenceInput(
            summary=summary,
            source_url=source_url,
            source_type=SourceType(source_type),
            verification_status=VerificationStatus(verification_status),
            checked_at=datetime.combine(evidence_date, evidence_time, tzinfo=JST),
            actually_used=actually_used,
            use_conditions=use_conditions,
            observations=observations,
        )
        with session_scope() as session:
            saved_evidence = save_evidence(
                session,
                current_product.id,
                evidence_payload,
                evidence_id=selected_evidence_id or None,
            )
        st.session_state[f"{evidence_selection_key}_next"] = saved_evidence.id
        st.toast("調査記録を保存しました。")
        st.rerun()
    except (ValidationError, ValueError) as exc:
        st.error(f"調査記録を保存できません：{exc}")

if current_product.evidence:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "確認内容": item.summary,
                    "出典種類": SOURCE_TYPE_LABELS.get(item.source_type, item.source_type),
                    "情報状態": VERIFICATION_LABELS.get(
                        item.verification_status, item.verification_status
                    ),
                    "実使用": "あり" if item.actually_used else "なし／未確認",
                    "確認日時": to_jst(item.checked_at),
                    "出典URL": item.source_url,
                }
                for item in current_product.evidence
            ]
        ),
        hide_index=True,
        column_config={
            "確認日時": st.column_config.DatetimeColumn(format="YYYY/MM/DD HH:mm"),
            "出典URL": st.column_config.LinkColumn(),
        },
    )
