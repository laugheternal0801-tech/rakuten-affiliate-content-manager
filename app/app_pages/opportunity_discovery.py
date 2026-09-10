from __future__ import annotations

import pandas as pd
import streamlit as st

from app.database import session_scope
from app.operating_system.discovery import discover_saved_opportunities

st.caption(
    "保存済みのSNS市場調査だけを再利用し、追加API費用なしで事業候補とHidden Nicheを抽出します。"
)

with session_scope() as session:
    candidates = discover_saved_opportunities(session)

with st.container(horizontal=True):
    st.metric("Candidates", len(candidates), border=True)
    st.metric(
        "High confidence",
        sum(candidate.confidence >= 0.7 for candidate in candidates),
        border=True,
    )
    st.metric("追加AI Cost", "$0.0000", border=True)

if not candidates:
    st.info(
        "完了したSNS市場調査レポートがまだありません。先に「SNS市場調査」で調査を保存してください。",
        icon=":material/travel_explore:",
    )
    st.stop()

minimum_confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.45, 0.05)
filtered = [item for item in candidates if item.confidence >= minimum_confidence]
st.dataframe(
    pd.DataFrame(
        [
            {
                "Opportunity": item.label,
                "Hidden niche": item.hidden_niche,
                "Score": item.score,
                "Confidence": item.confidence,
                "Evidence": len(item.evidence_ids),
                "Research run": item.research_run_id,
            }
            for item in filtered
        ]
    ),
    hide_index=True,
    width="stretch",
)

if filtered:
    selected = st.selectbox(
        "候補の詳細",
        filtered,
        format_func=lambda item: f"{item.score:.1f} · {item.label}",
    )
    with st.container(border=True):
        st.subheader(selected.label, anchor=False)
        st.write(selected.rationale)
        st.write(f"**Hidden Niche:** {selected.hidden_niche}")
        st.write(
            "**Evidence IDs:** "
            + (", ".join(selected.evidence_ids) if selected.evidence_ids else "未登録")
        )
        if st.button(
            "AI Decision Engineへ送る",
            type="primary",
            icon=":material/arrow_forward:",
        ):
            st.session_state["os_discovery_candidate"] = selected.model_dump(mode="json")
            st.success(
                "候補を保存しました。「AI Decision Engine」で同じResearch Runを選択してください。"
            )

st.caption(
    "候補は保存済みClaimの再ランキングです。外部検索やAI生成でEvidenceのない市場を作ってはいません。"
)
