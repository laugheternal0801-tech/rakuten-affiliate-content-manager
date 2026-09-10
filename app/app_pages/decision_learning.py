from __future__ import annotations

import pandas as pd
import streamlit as st

from app.database import session_scope
from app.operating_system.repositories import (
    calibration_summary,
    get_decision_outcome,
    list_decision_outcomes,
    list_decisions,
    list_predictions,
    model_performance_summaries,
    save_decision_outcome,
    save_prediction,
)
from app.operating_system.schemas import (
    DecisionOutcome,
    DecisionOutput,
    OutcomeErrorCause,
    OutcomeStatus,
    PredictionRecord,
)

st.caption(
    "予測と実績を同じDecisionへ紐付け、利益・Decision ROI・予測誤差・モデル実績を記録します。"
)

with session_scope() as session:
    decision_rows = list_decisions(session)
    outcomes = list_decision_outcomes(session)
    performance = model_performance_summaries(session)
    calibration = calibration_summary(session)

with st.container(horizontal=True):
    st.metric("Decisions", len(decision_rows), border=True)
    st.metric("Outcomes", len(outcomes), border=True)
    st.metric("Net Profit", f"${sum(item.net_profit for item in outcomes):,.2f}", border=True)
    st.metric("AI Cost", f"${sum(item.ai_cost for item in outcomes):,.4f}", border=True)

if decision_rows:
    selected_row = st.selectbox(
        "Decision",
        decision_rows,
        format_func=lambda row: f"{row.decision} · {row.opportunity_score:.1f} · {row.run_id[:16]}",
    )
    selected_decision = DecisionOutput.model_validate(selected_row.decision_json)
    with st.container(border=True):
        st.write(selected_decision.summary)
        st.caption(
            f"Decision ID: {selected_decision.decision_id} · Run ID: {selected_decision.run_id}"
        )

    prediction_col, outcome_col = st.columns(2)
    with prediction_col, st.form("prediction_record", border=True):
        st.subheader("Prediction", anchor=False)
        metric = st.text_input("Metric", value="gross_profit")
        predicted_value = st.number_input("Predicted value", value=0.0, step=100.0)
        prediction_confidence = st.slider("Prediction confidence", 0.0, 1.0, 0.6, 0.05)
        if st.form_submit_button("予測を記録", width="stretch"):
            with session_scope() as session:
                save_prediction(
                    session,
                    PredictionRecord(
                        decision_id=selected_decision.decision_id,
                        metric=metric,
                        predicted_value=predicted_value,
                        confidence=prediction_confidence,
                    ),
                )
            st.success("予測を記録しました。")
            st.rerun()

    with outcome_col, st.form("decision_outcome", border=True):
        st.subheader("Actual Outcome", anchor=False)
        status = st.selectbox("Outcome status", list(OutcomeStatus), format_func=lambda x: x.value)
        impressions = st.number_input("Impressions", min_value=0, value=0)
        clicks = st.number_input("Clicks", min_value=0, value=0)
        conversions = st.number_input("Conversions", min_value=0, value=0)
        revenue = st.number_input("Revenue", min_value=0.0, value=0.0, step=100.0)
        commission = st.number_input("Commission / gross profit", value=0.0, step=100.0)
        content_cost = st.number_input("Content cost", min_value=0.0, value=0.0)
        ad_cost = st.number_input("Ad cost", min_value=0.0, value=0.0)
        platform_cost = st.number_input("Platform cost", min_value=0.0, value=0.0)
        causes = st.multiselect(
            "Error causes",
            list(OutcomeErrorCause),
            format_func=lambda item: item.value,
        )
        lessons = st.text_area("Lessons（1行1件）")
        if st.form_submit_button("実績を記録", type="primary", width="stretch"):
            with session_scope() as session:
                saved = save_decision_outcome(
                    session,
                    DecisionOutcome(
                        decision_id=selected_decision.decision_id,
                        run_id=selected_decision.run_id,
                        status=status,
                        impressions=int(impressions),
                        clicks=int(clicks),
                        conversions=int(conversions),
                        revenue=revenue,
                        commission=commission,
                        content_cost=content_cost,
                        ad_cost=ad_cost,
                        platform_cost=platform_cost,
                        error_causes=causes,
                        lessons=[line.strip() for line in lessons.splitlines() if line.strip()],
                    ),
                )
            st.success(
                f"実績を保存しました。Net Profit ${saved.net_profit:,.2f} / "
                "Decision ROI "
                f"{saved.actual_decision_roi if saved.actual_decision_roi is not None else 'N/A'}"
            )
            st.rerun()

    with session_scope() as session:
        predictions = list_predictions(session, selected_decision.decision_id)
        current_outcome = get_decision_outcome(session, selected_decision.decision_id)
    if current_outcome:
        with st.container(horizontal=True):
            st.metric("Gross Profit", f"${current_outcome.gross_profit:,.2f}", border=True)
            st.metric("Total Cost", f"${current_outcome.total_cost:,.2f}", border=True)
            st.metric("Net Profit", f"${current_outcome.net_profit:,.2f}", border=True)
            st.metric(
                "Actual Decision ROI",
                (
                    f"{current_outcome.actual_decision_roi:.1%}"
                    if current_outcome.actual_decision_roi is not None
                    else "N/A"
                ),
                border=True,
            )
    if predictions:
        st.subheader("Prediction vs Actual", anchor=False)
        st.dataframe(
            pd.DataFrame([item.model_dump(mode="json") for item in predictions]),
            hide_index=True,
            width="stretch",
        )
else:
    st.info("AI Decision Engineで最初のDecisionを保存すると、ここで予測と実績を記録できます。")

st.subheader("Model Performance", anchor=False)
if performance:
    st.dataframe(
        pd.DataFrame([item.model_dump(mode="json") for item in performance]),
        hide_index=True,
        width="stretch",
    )
else:
    st.caption("モデル呼び出し実績はまだありません。")

with st.expander("Calibration"):
    if calibration:
        st.dataframe(pd.DataFrame(calibration), hide_index=True, width="stretch")
    else:
        st.caption("SUCCESS/FAILUREの実績が蓄積されると、Confidence帯ごとの成功率を表示します。")

st.caption(
    "Adaptive Routerは評価済みDecisionがモデルごとに5件以上になった時だけ、"
    "予測精度を品質値へ20%反映します。自動学習や重み変更は行いません。"
)
