from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from app.config import get_settings
from app.database import session_scope
from app.repositories import DEFAULT_SCORE_WEIGHTS, get_setting, set_setting
from app.schemas import ScoreWeights

settings = get_settings()
st.subheader("楽天API")
with st.container(border=True):
    if settings.rakuten_configured:
        st.success(
            "Application ID と Access Key が設定されています。", icon=":material/cloud_done:"
        )
    else:
        st.warning(
            "認証情報が未設定です。アプリはサンプルモードで動作します。", icon=":material/science:"
        )
    st.caption(f"公式商品検索API: {settings.rakuten_api_endpoint}")
    st.caption("認証値は画面・ログに表示しません。.envを編集後、アプリを再起動してください。")

with session_scope() as session:
    current_weights = get_setting(session, "score_weights", DEFAULT_SCORE_WEIGHTS)
    current_policy = get_setting(session, "pr_policy", "always")
    current_disclosure = get_setting(
        session,
        "affiliate_disclosure",
        "この記事にはアフィリエイト広告が含まれています。",
    )

st.subheader("商品評価の配点")
with st.form("score_settings"):
    row1 = st.columns(3)
    affiliate_rate = row1[0].number_input(
        "アフィリエイト料率", min_value=0.0, value=float(current_weights["affiliate_rate"])
    )
    review_count = row1[1].number_input(
        "レビュー件数", min_value=0.0, value=float(current_weights["review_count"])
    )
    review_average = row1[2].number_input(
        "平均評価", min_value=0.0, value=float(current_weights["review_average"])
    )
    row2 = st.columns(3)
    price_fit = row2[0].number_input(
        "希望価格帯との一致", min_value=0.0, value=float(current_weights["price_fit"])
    )
    free_shipping = row2[1].number_input(
        "送料無料", min_value=0.0, value=float(current_weights["free_shipping"])
    )
    keyword_match = row2[2].number_input(
        "キーワード一致度", min_value=0.0, value=float(current_weights["keyword_match"])
    )
    save_weights = st.form_submit_button("配点を保存", icon=":material/save:", type="primary")
if save_weights:
    try:
        weights = ScoreWeights(
            affiliate_rate=affiliate_rate,
            review_count=review_count,
            review_average=review_average,
            price_fit=price_fit,
            free_shipping=free_shipping,
            keyword_match=keyword_match,
        )
        with session_scope() as session:
            set_setting(session, "score_weights", weights.model_dump())
        st.success(
            f"配点を保存しました。入力値は100点満点へ正規化されます（入力合計 {weights.total:g}）。"
        )
    except ValidationError as exc:
        st.error(str(exc))

st.subheader("PR・広告表記")
policies = {
    "常にアフィリエイト広告表示を付ける": "always",
    "PR必須案件のみPR表示を付ける": "required_only",
    "手動選択": "manual",
}
current_label = next(label for label, value in policies.items() if value == current_policy)
with st.form("pr_settings"):
    policy_label = st.segmented_control("表記ルール", list(policies), default=current_label)
    disclosure = st.text_input("通常のアフィリエイト表示", value=current_disclosure)
    save_pr = st.form_submit_button("広告表記設定を保存", icon=":material/save:")
if save_pr:
    with session_scope() as session:
        set_setting(session, "pr_policy", policies[str(policy_label)])
        set_setting(session, "affiliate_disclosure", disclosure.strip())
    st.success("広告表記設定を保存しました。")

st.subheader("文章生成")
with st.container(border=True):
    if settings.llm_configured:
        st.badge("LLM拡張設定あり", icon=":material/extension:", color="green")
    else:
        st.badge("テンプレート生成", icon=":material/description:", color="blue")
    st.write("外部LLMが未設定でも全媒体のテンプレート下書きを生成できます。")
    st.caption(
        "初期実装は特定の有料LLMに依存しません。ContentGeneratorインターフェースから拡張できます。"
    )

st.subheader("安全設計")
st.markdown(
    """
- 楽天ID・パスワード・二段階認証コードは取得・保存しません。
- 商品情報は楽天公式APIだけから取得し、Webスクレイピングは行いません。
- 商品画像のダウンロード、切り抜き、文字入れ、ロゴ追加は行いません。
- 自動投稿・自動DM・自動コメント・自動リプライ機能はありません。
"""
)
