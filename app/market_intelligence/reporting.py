from __future__ import annotations

import html
import json
from collections import Counter

from app.market_intelligence.prompt_loader import load_prompt
from app.market_intelligence.schemas import (
    AgentOutput,
    AvailabilityStatus,
    Claim,
    ClaimVerification,
    EvidenceRecord,
    QuantitativeSummary,
    ResearchPlan,
    ResearchReport,
    ResearchRequest,
    SourceName,
)


def _claims_of_type(claims: list[Claim], *types: str) -> list[Claim]:
    wanted = set(types)
    return [claim for claim in claims if claim.type in wanted]


def _claim_line(claim: Claim) -> str:
    evidence = ", ".join(claim.evidence_ids) or "なし"
    return (
        f"- {claim.claim} "
        f"(Confidence: {claim.confidence.label} {claim.confidence.score:.2f}; "
        f"Verification: {claim.verification.value}; Evidence: {evidence})"
    )


def _section(title: str, claims: list[Claim]) -> list[str]:
    lines = [f"## {title}", ""]
    lines.extend(_claim_line(claim) for claim in claims)
    if not claims:
        lines.append("- 十分なEvidenceがありません。")
    lines.append("")
    return lines


class SynthesisAgent:
    """Builds a traceable report from Critic-verified claims only."""

    def synthesize(
        self,
        *,
        run_id: str,
        request: ResearchRequest,
        plan: ResearchPlan,
        critic_output: AgentOutput,
        quantitative: QuantitativeSummary,
        coverage: dict[SourceName, AvailabilityStatus],
        evidence: list[EvidenceRecord],
        historical_performance_signals: list[str] | None = None,
    ) -> ResearchReport:
        verified = critic_output.claims
        usable = [
            claim
            for claim in verified
            if claim.verification
            in {
                ClaimVerification.SUPPORTED,
                ClaimVerification.PARTIALLY_SUPPORTED,
            }
        ]
        ranked = sorted(usable, key=lambda claim: claim.confidence.score, reverse=True)
        low_or_rejected = [
            claim
            for claim in verified
            if claim.verification
            in {
                ClaimVerification.INSUFFICIENT_EVIDENCE,
                ClaimVerification.CONTRADICTED,
            }
        ]
        missing = [
            source.value
            for source, status in coverage.items()
            if source in request.preferred_sources and status is not AvailabilityStatus.AVAILABLE
        ]
        limitations = [
            "SNS利用者は市場全体の無作為標本ではなく、投稿者・利用者層の偏りがあります。",
            "Engagementは関心度の補助指標であり、売上・市場規模を直接表しません。",
            "欠損メトリクスは0に置換せず、未知値として保持しています。",
        ]
        if missing:
            limitations.append("取得不能・縮退した対象ソース: " + ", ".join(missing))
        if request.mock_mode:
            limitations.insert(
                0,
                "MOCK DATA: 本レポートは動作確認用の合成データであり、市場判断には利用できません。",
            )
        if historical_performance_signals:
            limitations.append(
                "Historical Performance Signalは過去Contentへの反応であり、"
                "市場需要や因果関係のEvidenceではありません。"
            )
        confidence_values = [claim.confidence.score for claim in verified]
        platform_analysis = {
            source: {
                "items": quantitative.platform_counts[source],
                "weighted_distribution": quantitative.platform_distribution[source],
                "coverage": coverage[source].value,
            }
            for source in SourceName
        }

        report = ResearchReport(
            research_run_id=run_id,
            title=f"{request.market} SNS Market Intelligence Report",
            executive_summary=(
                f"{quantitative.total_items}件の正規化標本から、Criticが"
                f"{len(usable)}件のClaimを支持または部分支持と判定しました。"
                "各結論はEvidence IDから元投稿へ追跡できます。"
            ),
            scope={
                "market": request.market,
                "purpose": request.purpose,
                "country": request.country,
                "region": request.region,
                "language": request.language,
                "date_from": request.date_from.isoformat(),
                "date_to": request.date_to.isoformat(),
                "depth": request.research_depth.value,
                "market_definition": plan.market_definition,
            },
            source_coverage=coverage,
            sample_size=quantitative.total_items,
            key_findings=ranked[:8],
            emerging_trends=_claims_of_type(usable, "emerging_trend"),
            consumer_needs=_claims_of_type(usable, "consumer_need"),
            pain_points=_claims_of_type(usable, "pain_point"),
            competitor_landscape=_claims_of_type(usable, "competitor_mention"),
            product_mentions=_claims_of_type(usable, "product_mention"),
            content_trends=_claims_of_type(usable, "content_trend"),
            opportunities=_claims_of_type(usable, "opportunity"),
            platform_analysis=platform_analysis,
            confidence_summary={
                "verified_claims": len(verified),
                "supported_or_partial": len(usable),
                "average_score": (
                    round(sum(confidence_values) / len(confidence_values), 4)
                    if confidence_values
                    else 0.0
                ),
                "method": "configured evidence-weighted formula",
            },
            risks=[
                "単一SNSや高Engagement投稿だけを市場全体へ一般化しないこと。",
                "商品化・広告判断の前に一次調査、販売データ、法務確認を追加すること。",
            ],
            counter_evidence=[claim.claim for claim in low_or_rejected],
            methodology=[
                "Source別Queryを並列実行し、Raw Dataを変更せず保存",
                "共通SocialItemへ正規化し、重複・Spam・Bot・広告らしさをスコア化",
                "Platform・新着・Engagement・肯定/否定を混ぜた層化標本を作成",
                "専門Agentを独立実行し、CriticがEvidence・反証・信頼度を検証",
                f"Synthesis prompt contract: {load_prompt('synthesis_agent.md').version}",
            ],
            data_limitations=limitations,
            quantitative=quantitative,
            evidence_ids=[record.evidence_id for record in evidence],
            historical_performance_signals=historical_performance_signals or [],
            is_mock=request.mock_mode,
        )
        return report.model_copy(update={"markdown": render_markdown(report)})


def render_markdown(report: ResearchReport) -> str:
    lines = [f"# {report.title}", ""]
    if report.is_mock:
        lines.extend(["> **MOCK DATA — 実データではありません**", ""])
    lines.extend(["## Executive Summary", "", report.executive_summary, ""])
    lines.extend(
        [
            "## Historical Performance Signals (Not Market Evidence)",
            "",
            *(
                [f"- {signal}" for signal in report.historical_performance_signals]
                or ["- 利用可能な過去Performance Signalはありません。"]
            ),
            "",
        ]
    )
    lines.extend(["## Scope", "", "```json"])
    lines.append(json.dumps(report.scope, ensure_ascii=False, indent=2))
    lines.extend(["```", ""])
    lines.extend(_section("Key Findings", report.key_findings))
    lines.extend(_section("Emerging Trends", report.emerging_trends))
    lines.extend(_section("Consumer Needs", report.consumer_needs))
    lines.extend(_section("Pain Points", report.pain_points))
    lines.extend(_section("Competitor Landscape", report.competitor_landscape))
    lines.extend(_section("Product Mentions", report.product_mentions))
    lines.extend(_section("Content Trends", report.content_trends))
    lines.extend(_section("Opportunities", report.opportunities))
    lines.extend(["## Platform Analysis", ""])
    for source, values in report.platform_analysis.items():
        lines.extend(
            [
                f"### {source.value}",
                "",
                f"- Items: {values.get('items', 0)}",
                f"- Weighted distribution: {values.get('weighted_distribution', 0)}",
                f"- Coverage: {values.get('coverage', 'unknown')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Confidence",
            "",
            "```json",
            json.dumps(report.confidence_summary, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    lines.extend(["## Risks", "", *[f"- {risk}" for risk in report.risks], ""])
    lines.extend(
        [
            "## Counter Evidence",
            "",
            *([f"- {value}" for value in report.counter_evidence] or ["- なし"]),
            "",
            "## Methodology",
            "",
            *[f"- {value}" for value in report.methodology],
            "",
            "## Data Limitations",
            "",
            *[f"- {value}" for value in report.data_limitations],
            "",
            "## Evidence Appendix",
            "",
            *[f"- {evidence_id}" for evidence_id in report.evidence_ids],
            "",
        ]
    )
    return "\n".join(lines)


def report_as_json(report: ResearchReport) -> str:
    return report.model_dump_json(indent=2)


def report_as_html(report: ResearchReport) -> str:
    paragraphs = []
    for block in report.markdown.split("\n\n"):
        escaped = html.escape(block).replace("\n", "<br>")
        paragraphs.append(f"<p>{escaped}</p>")
    mock_banner = (
        '<div role="alert"><strong>MOCK DATA — 実データではありません</strong></div>'
        if report.is_mock
        else ""
    )
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        f"<title>{html.escape(report.title)}</title></head><body>{mock_banner}"
        + "".join(paragraphs)
        + "</body></html>"
    )


def coverage_counts(
    coverage: dict[SourceName, AvailabilityStatus],
) -> dict[AvailabilityStatus, int]:
    return dict(Counter(coverage.values()))
