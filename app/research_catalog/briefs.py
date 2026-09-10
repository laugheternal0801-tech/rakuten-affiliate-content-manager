from __future__ import annotations

from typing import Any

from app.research_catalog.models import ResearchArticleBrief, ResearchPlan
from app.research_catalog.schemas import VerificationStatus

DEFAULT_DISCLOSURE_POLICY = "この記事にはアフィリエイト広告が含まれています。"


def _display_amount(value: Any, currency: str) -> str:
    if value is None:
        return "未確認"
    return f"{value} {currency}"


def _display_rate(value: Any) -> str:
    return "未確認" if value is None else f"{value}%"


def _primary_offer(offers: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((offer for offer in offers if offer.get("is_primary")), None) or (
        offers[0] if offers else None
    )


def build_article_brief(
    plan: ResearchPlan,
    disclosure_policy: str = DEFAULT_DISCLOSURE_POLICY,
) -> tuple[dict[str, Any], str]:
    products: list[dict[str, Any]] = []
    all_unverified: list[str] = []
    for link in plan.product_links:
        snapshot = dict(link.product_snapshot_json)
        product = dict(snapshot.get("product", {}))
        offers = [dict(item) for item in snapshot.get("offers", [])]
        evidence = [dict(item) for item in snapshot.get("evidence", [])]
        confirmed = [
            item
            for item in evidence
            if item.get("verification_status") == VerificationStatus.CONFIRMED_FACT.value
        ]
        unverified = [
            item
            for item in evidence
            if item.get("verification_status") != VerificationStatus.CONFIRMED_FACT.value
        ]
        offer = _primary_offer(offers)
        missing: list[str] = []
        if offer is None:
            missing.extend(["販売先", "価格", "送料", "報酬率", "アフィリエイトURL"])
        else:
            if offer.get("price") is None:
                missing.append("価格")
            if offer.get("shipping_fee") is None:
                missing.append("送料")
            if offer.get("commission_rate") is None:
                missing.append("報酬率")
            if not offer.get("affiliate_url"):
                missing.append("アフィリエイトURL")
            if offer.get("price_tax_status") == "unknown":
                missing.append("税込・税抜")
            if not offer.get("commission_base_notes"):
                missing.append("報酬対象額")
        missing.extend(
            f"調査記録: {item.get('summary', '')}"
            for item in unverified
            if item.get("summary")
        )
        all_unverified.extend(
            f"{product.get('product_name', '')}: {item}" for item in missing
        )
        products.append(
            {
                "product": product,
                "offer": offer,
                "all_offers": offers,
                "confirmed_evidence": confirmed,
                "unverified_evidence": unverified,
                "comparison_values": dict(link.comparison_values_json),
                "missing_items": missing,
                "actually_used": any(item.get("actually_used") for item in evidence),
            }
        )

    evaluation = {
        key: {
            "memo": str(value.get("memo", "")).strip() or "判定材料不足",
            "basis": str(value.get("basis", "")).strip() or "判定材料不足",
        }
        for key, value in dict(plan.evaluation_json).items()
    }
    payload: dict[str, Any] = {
        "brief_type": "deterministic_research_handoff",
        "plan_id": plan.plan_code,
        "plan_version": plan.version,
        "title": plan.title,
        "audience": plan.audience,
        "pain_point": plan.pain_point,
        "article_purpose": plan.article_purpose,
        "suggested_structure": [
            "読者の悩みとこの記事の目的",
            "選び方と比較軸",
            "商品比較",
            "商品ごとの確認済み情報・向く場面・注意点",
            "未確認事項と購入前の確認項目",
            "まとめ",
        ],
        "comparison_axes": list(plan.comparison_axes_json),
        "evaluation": evaluation,
        "adoption_reason": plan.adoption_reason,
        "weaknesses": plan.weaknesses,
        "additional_checks": plan.additional_checks,
        "products": products,
        "unverified_items": all_unverified,
        "disclosure_policy": disclosure_policy.strip() or DEFAULT_DISCLOSURE_POLICY,
        "automatic_posting": False,
    }
    return payload, render_article_brief_markdown(payload)


def _evidence_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in items:
        source = item.get("source_url") or "実測記録（URLなし）"
        usage = "実使用あり" if item.get("actually_used") else "実使用なし／未確認"
        lines.append(
            f"- {item.get('summary', '')}（状態: {item.get('verification_status', '')}、"
            f"出典種類: {item.get('source_type', '')}、{usage}、"
            f"確認日時: {item.get('checked_at') or '未確認'}、出典: {source}）"
        )
        if item.get("actually_used"):
            lines.append(f"  - 使用条件: {item.get('use_conditions') or '未記録'}")
            lines.append(f"  - 観察: {item.get('observations') or '未記録'}")
    return lines or ["- 判定材料不足"]


def render_article_brief_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 記事作成用ブリーフ｜{payload['title']}",
        "",
        f"- 企画ID: {payload['plan_id']}",
        f"- 企画版: {payload['plan_version']}",
        f"- 想定読者: {payload['audience']}",
        f"- 解決する悩み: {payload['pain_point']}",
        f"- 記事の目的: {payload['article_purpose'] or '選定商品の違いを根拠付きで伝える'}",
        f"- 広告表記方針: {payload['disclosure_policy']}",
        "- 自動投稿: 行わない",
        "",
        "## 構成案",
        "",
        *[f"{index}. {item}" for index, item in enumerate(payload["suggested_structure"], 1)],
        "",
        "## 比較軸",
        "",
        *(
            [f"- {axis}" for axis in payload["comparison_axes"]]
            or ["- 判定材料不足（比較軸を追加してください）"]
        ),
        "",
        "## 比較対象商品",
        "",
    ]
    for entry in payload["products"]:
        product = entry["product"]
        offer = entry["offer"]
        model_label = " / ".join(
            value
            for value in [product.get("model_number", ""), product.get("variant", "")]
            if value
        )
        lines.extend(
            [
                f"### {product.get('product_name', '')}",
                "",
                f"- 商品ID: {product.get('product_code', '')}",
                f"- メーカー: {product.get('manufacturer') or '未確認'}",
                f"- 型番・サイズ: {model_label or '未確認'}",
                f"- ジャンル: {product.get('genre') or '未設定'}",
                f"- 想定利用場面: {product.get('use_case') or '未設定'}",
                f"- 実使用: {'あり' if entry['actually_used'] else 'なし／未確認'}",
            ]
        )
        if offer is None:
            lines.append("- 販売条件: 未確認")
        else:
            currency = str(offer.get("currency") or "JPY")
            lines.extend(
                [
                    f"- 販売店: {offer.get('seller_name') or '未確認'}",
                    f"- 価格: {_display_amount(offer.get('price'), currency)}",
                    f"- 送料: {_display_amount(offer.get('shipping_fee'), currency)}",
                    f"- 税区分: {offer.get('price_tax_status') or 'unknown'}",
                    f"- 報酬率: {_display_rate(offer.get('commission_rate'))}",
                    f"- 報酬上限: {_display_amount(offer.get('commission_cap'), currency)}",
                    f"- 報酬条件: {offer.get('commission_conditions') or '未確認'}",
                    f"- 報酬対象額: {offer.get('commission_base_notes') or '未確認'}",
                    f"- 商品URL: {offer.get('product_url') or '未登録'}",
                    f"- アフィリエイトURL: {offer.get('affiliate_url') or '未登録'}",
                    f"- 条件確認日時: {offer.get('terms_checked_at') or '未確認'}",
                ]
            )
            other_offers = [
                candidate
                for candidate in entry["all_offers"]
                if candidate.get("id") != offer.get("id")
            ]
            if other_offers:
                lines.append("- その他の販売先:")
                for candidate in other_offers:
                    candidate_currency = str(candidate.get("currency") or "JPY")
                    candidate_price = _display_amount(
                        candidate.get("price"), candidate_currency
                    )
                    candidate_shipping = _display_amount(
                        candidate.get("shipping_fee"), candidate_currency
                    )
                    candidate_rate = _display_rate(candidate.get("commission_rate"))
                    candidate_url = (
                        candidate.get("affiliate_url")
                        or candidate.get("product_url")
                        or "未登録"
                    )
                    lines.append(
                        "  - "
                        f"{candidate.get('seller_name') or '販売店未確認'}／"
                        f"価格 {candidate_price}／送料 {candidate_shipping}／"
                        f"報酬率 {candidate_rate}／URL {candidate_url}／"
                        f"確認日時 {candidate.get('terms_checked_at') or '未確認'}"
                    )
        if entry["comparison_values"]:
            lines.append("- ジャンル固有の比較値:")
            lines.extend(
                f"  - {key}: {value or '判定材料不足'}"
                for key, value in entry["comparison_values"].items()
            )
        lines.extend(["", "確認済みの事実:", *_evidence_lines(entry["confirmed_evidence"])])
        lines.extend(
            [
                "",
                "仮説・未確認:",
                *_evidence_lines(entry["unverified_evidence"]),
                "",
                "追加確認:",
                *([f"- {item}" for item in entry["missing_items"]] or ["- なし"]),
                "",
            ]
        )

    lines.extend(["## 企画判断", ""])
    for label, value in payload["evaluation"].items():
        lines.extend(
            [
                f"### {label}",
                f"- 評価メモ: {value['memo']}",
                f"- 根拠: {value['basis']}",
                "",
            ]
        )
    lines.extend(
        [
            "## 採用理由・弱点",
            "",
            f"- 採用理由: {payload['adoption_reason'] or '判定材料不足'}",
            f"- 弱点: {payload['weaknesses'] or '判定材料不足'}",
            f"- 追加確認事項: {payload['additional_checks'] or 'なし'}",
            "",
            "## 制作時の注意",
            "",
            "- 確認済みの事実、メーカー訴求、実使用の観察を区別する。",
            "- 仮説と未確認事項を事実として断定しない。",
            "- 価格・送料・報酬条件は公開直前に再確認する。",
            "- この記事ブリーフは記事生成済みを意味しない。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def brief_handoff_payload(
    brief: ResearchArticleBrief,
    *,
    channel: str,
) -> dict[str, Any]:
    products = list(brief.brief_json.get("products", []))
    legacy_ids = [
        int(product["product"]["legacy_product_id"])
        for product in products
        if product.get("product", {}).get("legacy_product_id") is not None
    ]
    if channel == "Pinterest":
        if brief.published_match_confirmed_at is None:
            raise ValueError("Pinterest制作へ渡す前に公開済みnote記事との一致確認が必要です")
        if not (
            brief.published_article_url
            and brief.published_article_title
            and brief.published_article_summary
        ):
            raise ValueError("公開済みnote記事のURL・タイトル・要約が不足しています")
        custom_message = (
            "公開済みnote記事をPinterest向けに展開します。\n"
            f"記事URL: {brief.published_article_url}\n"
            f"記事タイトル: {brief.published_article_title}\n"
            f"記事要約: {brief.published_article_summary}\n"
            "企画にない事実や未公開URLは追加しないでください。"
        )
        theme = brief.published_article_title
    else:
        custom_message = (
            "以下は登録情報から決定論的に生成した記事作成用ブリーフです。"
            "確認済みの事実と、仮説・未確認事項を区別して使用してください。\n\n"
            + brief.markdown
        )
        theme = str(brief.brief_json.get("title", brief.title))
    return {
        "brief_id": brief.id,
        "brief_code": brief.brief_code,
        "channel": channel,
        "theme": theme,
        "target_audience": str(brief.brief_json.get("audience", "")),
        "pain_point": str(brief.brief_json.get("pain_point", "")),
        "product_ids": legacy_ids,
        "custom_message": custom_message,
        "markdown": brief.markdown,
        "published_article_url": brief.published_article_url,
        "published_article_title": brief.published_article_title,
        "published_article_summary": brief.published_article_summary,
    }
