from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.market_intelligence.connectors.base import ConnectorError, ConnectorQuery
from app.market_intelligence.connectors.registry import SourceRegistry
from app.market_intelligence.schemas import (
    AvailabilityStatus,
    RawSocialItem,
    ResearchPlan,
    ResearchRequest,
    SocialItem,
    SourceName,
    SourceRunResult,
    StructuredError,
)

SourceProgressHandler = Callable[[SourceName, str, int], None]


@dataclass
class QueryCache:
    values: dict[str, list[RawSocialItem]]

    def __init__(self) -> None:
        self.values = {}

    def get(self, key: str) -> list[RawSocialItem] | None:
        return self.values.get(key)

    def put(self, key: str, items: list[RawSocialItem]) -> None:
        self.values[key] = items


class SourceOrchestrator:
    def __init__(
        self,
        registry: SourceRegistry,
        *,
        max_concurrency: int = 5,
        cache: QueryCache | None = None,
    ) -> None:
        self._registry = registry
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cache = cache or QueryCache()

    async def collect(
        self,
        run_id: str,
        request: ResearchRequest,
        plan: ResearchPlan,
        on_progress: SourceProgressHandler | None = None,
    ) -> tuple[dict[SourceName, SourceRunResult], dict[SourceName, str]]:
        health = await self._registry.health_check_all()
        selected = set(request.preferred_sources)

        async def collect_source(source: SourceName) -> SourceRunResult:
            if source not in selected:
                return SourceRunResult(
                    source=source,
                    status=AvailabilityStatus.NOT_CONFIGURED,
                    error=StructuredError(
                        code="NOT_SELECTED",
                        message="今回の調査対象外です。",
                        source=source,
                    ),
                )
            source_health = health[source]
            if source_health.status is not AvailabilityStatus.AVAILABLE:
                return SourceRunResult(
                    source=source,
                    status=source_health.status,
                    error=StructuredError(
                        code="NOT_CONFIGURED",
                        message=source_health.message,
                        source=source,
                    ),
                )
            source_plan = plan.source_plans.get(source)
            if source_plan is None:
                return SourceRunResult(
                    source=source,
                    status=AvailabilityStatus.ERROR,
                    error=StructuredError(
                        code="PLAN_MISSING",
                        message="DirectorのSource Planがありません。",
                        source=source,
                    ),
                )
            if on_progress:
                on_progress(source, "collecting", 0)
            started = time.perf_counter()
            items: list[RawSocialItem] = []
            queries_attempted = 0
            any_cache = False
            try:
                async with self._semaphore:
                    connector = self._registry.get(source)
                    for text in source_plan.queries:
                        if len(items) >= source_plan.target_items:
                            break
                        connector_query = ConnectorQuery(
                            research_run_id=run_id,
                            market=request.market,
                            query=text,
                            language=request.language,
                            date_from=request.date_from,
                            date_to=request.date_to,
                            max_items=min(
                                request.max_items_per_source - len(items),
                                source_plan.target_items - len(items),
                            ),
                            parameters=source_plan.parameters,
                        )
                        queries_attempted += 1
                        cache_key = f"{source.value}:{connector_query.cache_key}"
                        cached = self._cache.get(cache_key)
                        if cached is not None:
                            items.extend(cached)
                            any_cache = True
                        else:
                            found = await connector.search(connector_query)
                            self._cache.put(cache_key, found)
                            items.extend(found)
                        if on_progress:
                            on_progress(source, "collecting", len(items))
                duration = int((time.perf_counter() - started) * 1000)
                status = AvailabilityStatus.AVAILABLE if items else AvailabilityStatus.DEGRADED
                return SourceRunResult(
                    source=source,
                    status=status,
                    items=items[: request.max_items_per_source],
                    queries_attempted=queries_attempted,
                    duration_ms=duration,
                    from_cache=any_cache,
                )
            except ConnectorError as exc:
                return SourceRunResult(
                    source=source,
                    status=AvailabilityStatus.DEGRADED,
                    items=items,
                    queries_attempted=queries_attempted,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=StructuredError(
                        code=exc.code,
                        message=str(exc),
                        source=source,
                        retryable=exc.retryable,
                        details={"status_code": exc.status_code},
                    ),
                )
            except Exception as exc:  # connector isolation boundary
                return SourceRunResult(
                    source=source,
                    status=AvailabilityStatus.ERROR,
                    items=items,
                    queries_attempted=queries_attempted,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=StructuredError(
                        code="UNEXPECTED_CONNECTOR_ERROR",
                        message=f"{source.value} Connectorで{type(exc).__name__}が発生しました。",
                        source=source,
                    ),
                )

        gathered = await asyncio.gather(
            *(collect_source(source) for source in self._registry.sources)
        )
        results = {result.source: result for result in gathered}
        coverage = {source: result.status.value for source, result in results.items()}
        return results, coverage

    async def normalize(
        self,
        request: ResearchRequest,
        source_results: dict[SourceName, SourceRunResult],
    ) -> list[SocialItem]:
        async def normalize_item(raw: RawSocialItem) -> SocialItem:
            connector = self._registry.get(raw.platform)
            return await connector.normalize(raw, request.market)

        tasks = [normalize_item(raw) for result in source_results.values() for raw in result.items]
        if not tasks:
            return []
        normalized = await asyncio.gather(*tasks, return_exceptions=True)
        return [item for item in normalized if isinstance(item, SocialItem)]
