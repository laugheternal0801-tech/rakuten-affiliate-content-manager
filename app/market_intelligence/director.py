from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from app.market_intelligence.prompt_loader import load_prompt
from app.market_intelligence.schemas import (
    ALL_SOURCES,
    ResearchDepth,
    ResearchPlan,
    ResearchRequest,
    SourceName,
    SourcePlan,
)
from app.services.ai_council import LLMProvider, LLMRequest

DEPTH_QUERY_COUNT = {
    ResearchDepth.QUICK: 1,
    ResearchDepth.STANDARD: 3,
    ResearchDepth.DEEP: 5,
}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class RuleBasedResearchDirector:
    """Creates a reproducible source-specific plan without inventing market facts."""

    def create_plan(
        self,
        request: ResearchRequest,
        performance_signals: list[str] | None = None,
    ) -> ResearchPlan:
        query_count = DEPTH_QUERY_COUNT[request.research_depth]
        keywords = _unique([request.market, *request.seed_keywords, *request.competitors])
        base = request.market
        source_queries: dict[SourceName, list[str]] = {
            SourceName.X: [
                base,
                f'"{base}" 評判 OR 不満',
                f'"{base}" おすすめ OR 比較',
                f'"{base}" 欲しい OR 困る',
                f'"{base}" 新商品 OR トレンド',
            ],
            SourceName.REDDIT: [
                f"{base} recommend problem experience",
                f"{base} worth it",
                f"{base} vs alternative",
                f"{base} complaint",
                f"{base} buying advice",
            ],
            SourceName.YOUTUBE: [
                f"{base} review",
                f"{base} comparison",
                f"{base} how to",
                f"{base} best",
                f"{base} problems",
            ],
            SourceName.TIKTOK: [
                f"{base} trend",
                f"{base} review",
                f"{base} hack",
                f"{base} comparison",
                f"{base} problem",
            ],
            SourceName.INSTAGRAM: [
                base,
                f"{base} 使用例",
                f"{base} レビュー",
                f"{base} 比較",
                f"{base} トレンド",
            ],
            SourceName.PINTEREST: [
                f"{base} idea",
                f"{base} inspiration",
                f"{base} style",
                f"{base} trend",
                f"{base} guide",
            ],
            SourceName.WEB: [
                f"{base} 市場 統計",
                f"{base} 企業 公式",
                f"{base} 業界 動向",
                f"{base} ニュース",
                f"{base} 調査 レポート",
            ],
        }
        target = min(
            request.max_items_per_source,
            {ResearchDepth.QUICK: 20, ResearchDepth.STANDARD: 100, ResearchDepth.DEEP: 500}[
                request.research_depth
            ],
        )
        source_plans = {
            source: SourcePlan(
                source=source,
                queries=_unique(source_queries[source] + request.seed_keywords)[:query_count],
                rationale=f"{source.value}固有の検索意図へ最適化",
                target_items=target,
                parameters={
                    "region_code": "JP" if request.country.casefold() in {"japan", "日本"} else "US"
                },
            )
            for source in ALL_SOURCES
        }
        return ResearchPlan(
            market=request.market,
            objective=request.purpose,
            market_definition=(
                f"{request.country} {request.region}における「{request.market}」を、"
                f"{request.date_from.isoformat()}から{request.date_to.isoformat()}まで調査する。"
            ),
            research_questions=[
                "現在どのテーマが継続的に増えているか",
                "消費者の未解決課題と購入障壁は何か",
                "競合の強み・弱み・比較軸は何か",
                "商品・コンテンツの機会はどこにあるか",
            ],
            hypotheses=[
                f"{request.market}にはSNS横断で反復される未充足ニーズがある",
                "高Engagement投稿と市場全体の傾向は一致しない可能性がある",
            ],
            keywords=keywords,
            related_keywords=_unique(["比較", "評判", "不満", "おすすめ", "価格", "使い方"]),
            excluded_keywords=request.excluded_keywords,
            competitors=request.competitors,
            product_candidates=[],
            pain_point_candidates=["価格", "使いやすさ", "継続負担", "情報不足"],
            source_plans=source_plans,
            required_data_volume=target * len(request.preferred_sources),
            analysis_strategy=[
                "Raw Dataを先に保存する",
                "重複・Spam・Bot候補を削除せずスコア化する",
                "専門Agentを独立実行する",
                "ClaimをEvidenceへ紐付けてCriticが検証する",
                "Source CoverageとSNS Biasを明示する",
                "Historical Performance Signalを市場Evidenceと分離して検証仮説に使う",
            ],
            historical_performance_signals=performance_signals or [],
        )


class LLMResearchDirector:
    def __init__(self, provider: LLMProvider, model: str) -> None:
        self._provider = provider
        self._model = model

    def create_plan(
        self,
        request: ResearchRequest,
        performance_signals: list[str] | None = None,
    ) -> ResearchPlan:
        prompt = load_prompt("research_director.md")
        result = self._provider.generate_structured(
            LLMRequest(
                system_prompt=prompt.content,
                user_prompt=(
                    "次のResearchRequestからResearchPlanを作成してください。\n"
                    "全7ソースのsource_plansを必ず含めてください。\n\n"
                    "Historical Performance Signalは市場需要のEvidenceではなく、"
                    "次回検証する仮説としてのみ扱ってください。\n\n"
                    + json.dumps(
                        {
                            "research_request": request.model_dump(mode="json"),
                            "historical_performance_signals": performance_signals or [],
                        },
                        ensure_ascii=False,
                    )
                ),
                max_output_tokens=4_000,
                metadata={"stage": "research_director", "market": request.market},
            ),
            self._model,
            ResearchPlan.model_json_schema(),
            schema_name="research_plan",
        )
        plan = ResearchPlan.model_validate(result)
        missing = [source for source in ALL_SOURCES if source not in plan.source_plans]
        if missing:
            raise ValueError(
                "Research Directorの計画に不足があります: "
                + ", ".join(source.value for source in missing)
            )
        return plan.model_copy(update={"historical_performance_signals": performance_signals or []})


def choose_director(
    provider_models: Mapping[str, str],
    provider_lookup: Callable[[str], LLMProvider] | None = None,
) -> RuleBasedResearchDirector | LLMResearchDirector:
    if provider_models and provider_lookup is not None:
        provider_name = next(iter(provider_models))
        return LLMResearchDirector(provider_lookup(provider_name), provider_models[provider_name])
    return RuleBasedResearchDirector()
