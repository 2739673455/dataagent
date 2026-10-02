"""元数据语义召回用例：目录读取、权限过滤、混合检索与上下文补全。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from loguru import logger

from app.identity.contracts import AssetAccessPolicy
from app.metadata.contracts import (
    ColumnKey,
    SemanticColumnRecallResult,
    SemanticIndexStatus,
    SemanticMatchReason,
    SemanticMetricRecallResult,
    SemanticRecallFailure,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticResourceType,
    SemanticTableContext,
    SemanticValueRecallResult,
    column_reference_key,
)
from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.semantic_index import SemanticIndexRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.contracts.search import SearchHit

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch

    from app.shared.clients.embedding_client import EmbeddingClient

_RRF_K = 60
_INDEX_SEARCH_LIMIT_MULTIPLIER = 3
_MAX_RANKED_CONTEXT_COLUMNS = 30
_COLUMN_EXAMPLE_LIMIT = 3
_DEFAULT_INDEX_QUERY_CONCURRENCY = 8

CandidateKeyT = TypeVar("CandidateKeyT")
IndexResultT = TypeVar("IndexResultT")
ValueKey = tuple[str, str, str]
ValueSyncStatus = Literal["syncing", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class SemanticCatalog:
    """语义召回使用的完整元数据目录。"""

    tables: dict[str, TableInfo]
    columns: dict[ColumnKey, ColumnInfo]
    metrics: dict[str, MetricInfo]


@dataclass(slots=True)
class _CandidateScore:
    """候选资源的融合分数和命中依据。"""

    score: float = 0.0
    reasons: list[SemanticMatchReason] = field(default_factory=list)

    def add(self, score: float, reason: SemanticMatchReason) -> None:
        """累计分数并稳定去重命中依据。"""
        self.score += score
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass(slots=True)
class _RecallContext:
    """单次语义召回的输入、目录和可变状态。"""

    request: SemanticResourceRecallRequest
    catalog: SemanticCatalog
    index_query_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(_DEFAULT_INDEX_QUERY_CONCURRENCY)
    )
    column_scores: dict[ColumnKey, _CandidateScore] = field(default_factory=dict)
    metric_scores: dict[str, _CandidateScore] = field(default_factory=dict)
    value_scores: dict[ValueKey, _CandidateScore] = field(default_factory=dict)
    failures: list[SemanticRecallFailure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def search_limit(self) -> int:
        """计算索引层候选扩召数量。"""
        return self.request.limit_per_type * _INDEX_SEARCH_LIMIT_MULTIPLIER

    def record_backend_failure(
        self,
        backend_name: str,
        error: BaseException,
        *,
        resource_type: SemanticResourceType,
        channel: Literal["fulltext", "vector"],
        term: str | None = None,
    ) -> None:
        """记录检索失败范围并保留任务取消语义。"""
        if not isinstance(error, Exception):
            raise error
        failure_scope = (
            f"{resource_type}/{channel}/{backend_name}, term={term}"
            if term is not None
            else f"{resource_type}/{channel}/{backend_name}"
        )
        logger.opt(exception=error).warning(f"语义召回后端失败: {failure_scope}")
        failure = SemanticRecallFailure(
            resource_type=resource_type,
            channel=channel,
            term=term,
        )
        if failure not in self.failures:
            self.failures.append(failure)


@dataclass(frozen=True, slots=True)
class _RankedCandidates:
    """三类资源的融合排名结果。"""

    columns: list[tuple[ColumnKey, float, list[SemanticMatchReason]]]
    metrics: list[tuple[str, float, list[SemanticMatchReason]]]
    values: list[tuple[ValueKey, float, list[SemanticMatchReason]]]
    truncated: bool


@dataclass(slots=True)
class _ColumnContext:
    """待返回字段及其引入原因。"""

    info: ColumnInfo
    inclusion_reasons: list[str]
    rank_score: float | None = None
    match_reasons: list[SemanticMatchReason] = field(default_factory=list)


class SemanticResourceService:
    """读取本次目录并完成语义召回，外部检索期间不持有数据库会话。"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        es: AsyncElasticsearch,
        embedding: EmbeddingClient,
        *,
        max_concurrent_index_queries: int = _DEFAULT_INDEX_QUERY_CONCURRENCY,
    ) -> None:
        """绑定元数据存储、语义索引和向量客户端，配置单次检索并发上限。"""
        if max_concurrent_index_queries <= 0:
            raise ValueError("max_concurrent_index_queries 必须为正整数")
        self._postgres = postgres
        self._embedding_client = embedding
        self._column_repo = ColumnESRepo(es)
        self._metric_repo = MetricESRepo(es)
        self._value_repo = ValueESRepo(es)
        self._max_concurrent_index_queries = max_concurrent_index_queries

    async def recall(
        self,
        request: SemanticResourceRecallRequest,
        policy: AssetAccessPolicy,
    ) -> SemanticResourceRecallResponse:
        """按目录权限过滤、外部检索和响应构建三个阶段完成召回。"""
        async with self._postgres.session() as session:
            repo = MetaPGRepo(session)
            tables = await repo.list_table_infos()
            columns = await repo.list_column_infos()
            metrics = await repo.list_metric_infos()
        context = self._create_context(
            request,
            SemanticCatalog(
                tables={item.name: item for item in tables},
                columns={(item.t_name, item.name): item for item in columns},
                metrics={item.name: item for item in metrics},
            ),
            policy,
        )
        await self._retrieve(context)
        return self._build_response(context)

    def _create_context(
        self,
        request: SemanticResourceRecallRequest,
        catalog: SemanticCatalog,
        policy: AssetAccessPolicy,
    ) -> _RecallContext:
        """按权限过滤已加载的目录并创建单次检索上下文。"""
        authorization_filter = MetadataAuthorizationFilter(
            policy, cfg.query.data_source, cfg.doris.database
        )
        table_infos = list(catalog.tables.values())
        column_infos = list(catalog.columns.values())
        metric_infos = list(catalog.metrics.values())
        allowed_column_keys = authorization_filter.allowed_column_keys(column_infos)
        allowed_columns = {
            (item.t_name, item.name): item
            for item in authorization_filter.filter_columns(
                column_infos,
                allowed_column_keys,
            )
        }
        visible_tables = {
            item.name: item
            for item in authorization_filter.filter_tables(
                table_infos,
                allowed_column_keys,
            )
        }
        allowed_metrics = {
            item.name: item
            for item in authorization_filter.filter_metrics(
                metric_infos,
                allowed_column_keys,
            )
        }
        return _RecallContext(
            request=request,
            catalog=SemanticCatalog(
                tables=visible_tables,
                columns=allowed_columns,
                metrics=allowed_metrics,
            ),
            index_query_semaphore=asyncio.Semaphore(self._max_concurrent_index_queries),
        )

    async def _retrieve(self, context: _RecallContext) -> None:
        """生成共享向量，再按字段、指标、取值顺序执行召回。"""
        search_columns = "column" in context.request.resource_types and bool(
            context.catalog.columns
        )
        search_metrics = "metric" in context.request.resource_types and bool(
            context.catalog.metrics
        )
        embeddings: list[list[float]] | None = None
        if search_columns or search_metrics:
            try:
                embeddings = await self._embedding_client.aembed_documents(
                    context.request.terms
                )
            except Exception as exc:  # noqa: BLE001
                if search_columns:
                    context.record_backend_failure(
                        "向量生成", exc, resource_type="column", channel="vector"
                    )
                if search_metrics:
                    context.record_backend_failure(
                        "向量生成", exc, resource_type="metric", channel="vector"
                    )
        if search_columns:
            await self._collect_semantic_matches(
                context,
                embeddings,
                repo=self._column_repo,
                resource_type="column",
                allowed_keys=frozenset(context.catalog.columns),
                scores=context.column_scores,
                key_of=lambda item: (item.t_name, item.name),
                backend_label="字段",
            )
        if search_metrics:
            await self._collect_semantic_matches(
                context,
                embeddings,
                repo=self._metric_repo,
                resource_type="metric",
                allowed_keys=frozenset(context.catalog.metrics),
                scores=context.metric_scores,
                key_of=lambda item: item.name,
                backend_label="指标",
            )
        if "value" in context.request.resource_types and context.catalog.columns:
            await self._collect_value_matches(context)

    async def _collect_semantic_matches[ItemT, KeyT](
        self,
        context: _RecallContext,
        embeddings: list[list[float]] | None,
        *,
        repo: SemanticIndexRepo[ItemT, KeyT],
        resource_type: Literal["column", "metric"],
        allowed_keys: frozenset[KeyT],
        scores: dict[KeyT, _CandidateScore],
        key_of: Callable[[ItemT], KeyT],
        backend_label: str,
    ) -> None:
        """共用并发查询、通道降级和按原始排名融合的流程。"""
        channels: list[Literal["fulltext", "vector"]] = ["fulltext"]
        if embeddings is not None:
            channels.append("vector")
        for channel in channels:
            operations = (
                [
                    repo.search_text_hits(
                        term, allowed_keys=allowed_keys, limit=context.search_limit
                    )
                    for term in context.request.terms
                ]
                if channel == "fulltext"
                else [
                    repo.search_vector_hits(
                        embedding, allowed_keys=allowed_keys, limit=context.search_limit
                    )
                    for embedding in embeddings or []
                ]
            )
            results = await asyncio.gather(
                *(
                    self._run_index_query(context, operation)
                    for operation in operations
                ),
                return_exceptions=True,
            )
            self._merge_hits(
                context,
                results,
                resource_type=resource_type,
                key_of=key_of,
                allowed_keys=allowed_keys,
                scores=scores,
                backend_name=backend_label
                + ("全文" if channel == "fulltext" else "向量"),
                match_type=channel,
            )

    async def _collect_value_matches(
        self,
        context: _RecallContext,
    ) -> None:
        """收集字段值全文索引命中。"""
        allowed_columns = frozenset(context.catalog.columns)
        results = await asyncio.gather(
            *(
                self._run_index_query(
                    context,
                    self._value_repo.search_hits(
                        term,
                        allowed_columns=allowed_columns,
                        limit=context.search_limit,
                    ),
                )
                for term in context.request.terms
            ),
            return_exceptions=True,
        )
        for term, result in zip(context.request.terms, results, strict=True):
            if isinstance(result, BaseException):
                context.record_backend_failure(
                    "字段取值全文",
                    result,
                    resource_type="value",
                    channel="fulltext",
                    term=term,
                )
                continue
            for rank, hit in enumerate(result, start=1):
                column_key = (hit.item.t_name, hit.item.c_name)
                column_info = context.catalog.columns.get(column_key)
                if column_info is None or not column_info.index_values:
                    continue
                key = (hit.item.t_name, hit.item.c_name, hit.item.value)
                self._add_candidate_score(
                    context.value_scores,
                    key,
                    self._rrf_score(rank),
                    SemanticMatchReason(
                        match_type="fulltext",
                        term=term,
                        score=hit.score,
                    ),
                )

    async def _run_index_query(
        self,
        context: _RecallContext,
        operation: Awaitable[IndexResultT],
    ) -> IndexResultT:
        """限制本次召回的索引查询并发量。"""
        async with context.index_query_semaphore:
            return await operation

    def _merge_hits[ItemT, KeyT](
        self,
        context: _RecallContext,
        results: list[list[SearchHit[ItemT]] | BaseException],
        *,
        resource_type: Literal["column", "metric"],
        key_of: Callable[[ItemT], KeyT],
        allowed_keys: Collection[KeyT],
        scores: dict[KeyT, _CandidateScore],
        backend_name: str,
        match_type: Literal["fulltext", "vector"],
    ) -> None:
        """校验并融合字段或指标命中，按每词原始排名计分并保留命中依据。"""
        for term, result in zip(context.request.terms, results, strict=True):
            if isinstance(result, BaseException):
                context.record_backend_failure(
                    backend_name,
                    result,
                    resource_type=resource_type,
                    channel=match_type,
                    term=term,
                )
                continue
            seen_keys: set[KeyT] = set()
            for rank, hit in enumerate(result, start=1):
                key = key_of(hit.item)
                if key not in allowed_keys or key in seen_keys:
                    continue
                seen_keys.add(key)
                self._add_candidate_score(
                    scores,
                    key,
                    self._rrf_score(rank),
                    SemanticMatchReason(
                        match_type=match_type,
                        term=term,
                        score=hit.score,
                    ),
                )

    @staticmethod
    def _add_candidate_score(
        scores: dict[CandidateKeyT, _CandidateScore],
        key: CandidateKeyT,
        score: float,
        reason: SemanticMatchReason,
    ) -> None:
        """新增或合并候选资源分数。"""
        scores.setdefault(key, _CandidateScore()).add(score, reason)

    @staticmethod
    def _rrf_score(rank: int) -> float:
        """计算倒数排名融合分数。"""
        return 1 / (_RRF_K + rank)

    def _build_response(
        self,
        context: _RecallContext,
    ) -> SemanticResourceRecallResponse:
        """融合候选排名并组装最终语义召回响应。"""
        ranked = self._rank_context(context)
        metric_results = self._build_metric_results(ranked.metrics, context)
        value_results = self._build_value_results(ranked.values, context)
        (
            column_results,
            table_contexts,
            context_truncated,
        ) = _ColumnContextBuilder(
            context.catalog,
            context.warnings,
        ).build(ranked)
        return SemanticResourceRecallResponse(
            status="partial" if context.failures else "success",
            recall_id=f"recall_{uuid.uuid4().hex}",
            terms=context.request.terms,
            metrics=metric_results,
            columns=column_results,
            values=value_results,
            tables=table_contexts,
            failures=context.failures,
            warnings=context.warnings,
            truncated=ranked.truncated or context_truncated,
        )

    def _rank_context(self, context: _RecallContext) -> _RankedCandidates:
        """对三类候选执行类型内融合排名。"""
        columns, columns_truncated = self._rank_candidates(
            context.column_scores,
            context.request.limit_per_type,
        )
        metrics, metrics_truncated = self._rank_candidates(
            context.metric_scores,
            context.request.limit_per_type,
        )
        values, values_truncated = self._rank_candidates(
            context.value_scores,
            context.request.limit_per_type,
        )
        return _RankedCandidates(
            columns=columns,
            metrics=metrics,
            values=values,
            truncated=(columns_truncated or metrics_truncated or values_truncated),
        )

    @staticmethod
    def _rank_candidates(
        scores: dict[CandidateKeyT, _CandidateScore],
        limit: int,
    ) -> tuple[
        list[tuple[CandidateKeyT, float, list[SemanticMatchReason]]],
        bool,
    ]:
        """按融合分数排序并归一化为类型内相对排名分数，分数不表示概率。"""
        ordered = sorted(
            scores.items(),
            key=lambda item: (-item[1].score, str(item[0])),
        )
        if not ordered:
            return [], False
        max_score = ordered[0][1].score
        ranked = [
            (
                key,
                round(candidate.score / max_score, 6),
                candidate.reasons,
            )
            for key, candidate in ordered[:limit]
        ]
        return ranked, len(ordered) > limit

    def _build_metric_results(
        self,
        ranked_metrics: list[tuple[str, float, list[SemanticMatchReason]]],
        context: _RecallContext,
    ) -> list[SemanticMetricRecallResult]:
        """构建指标检索响应。"""
        results: list[SemanticMetricRecallResult] = []
        for name, rank_score, match_reasons in ranked_metrics:
            metric_info = context.catalog.metrics[name]
            index_status = _index_status(metric_info)
            if index_status != "current" and match_reasons:
                context.warnings.append(f"指标语义索引状态为 {index_status}: {name}")
            results.append(
                SemanticMetricRecallResult(
                    name=metric_info.name,
                    description=metric_info.description,
                    alias=metric_info.alias,
                    relevant_columns=metric_info.relevant_columns,
                    rank_score=rank_score,
                    match_reasons=match_reasons,
                    meta_version=metric_info.meta_version,
                    index_version=metric_info.index_version,
                    index_status=index_status,
                )
            )
        return results

    def _build_value_results(
        self,
        ranked_values: list[tuple[ValueKey, float, list[SemanticMatchReason]]],
        context: _RecallContext,
    ) -> list[SemanticValueRecallResult]:
        """构建字段值检索响应。"""
        results: list[SemanticValueRecallResult] = []
        warned_columns: set[ColumnKey] = set()
        for (t_name, c_name, value), rank_score, match_reasons in ranked_values:
            column_info = context.catalog.columns[(t_name, c_name)]
            state = column_info.value_index_state
            sync_status = self._value_sync_status(
                state.status if state is not None else None
            )
            if sync_status != "succeeded" and (t_name, c_name) not in warned_columns:
                context.warnings.append(
                    f"字段取值索引状态为 {sync_status or '未知'}: {t_name}.{c_name}"
                )
                warned_columns.add((t_name, c_name))
            results.append(
                SemanticValueRecallResult(
                    value=value,
                    t_name=t_name,
                    c_name=c_name,
                    rank_score=rank_score,
                    match_reasons=match_reasons,
                    sync_status=sync_status,
                    synced_at=state.last_synced_at if state is not None else None,
                )
            )
        return results

    @staticmethod
    def _value_sync_status(status: str | None) -> ValueSyncStatus | None:
        """将数据库字段值同步状态收窄到响应枚举。"""
        if status in {"syncing", "succeeded", "failed"}:
            return cast(ValueSyncStatus, status)
        return None


class _ColumnContextBuilder:
    """按融合排名补充字段、一层外键及参与表的主键上下文。"""

    def __init__(
        self,
        catalog: SemanticCatalog,
        warnings: list[str],
    ) -> None:
        """初始化语义目录、告警集合和字段上下文缓存。"""
        self._catalog = catalog
        self._warnings = warnings
        self._contexts: dict[ColumnKey, _ColumnContext] = {}
        self._ranked_context_count = 0
        self._truncated = False

    def build(
        self,
        ranked: _RankedCandidates,
    ) -> tuple[
        list[SemanticColumnRecallResult],
        list[SemanticTableContext],
        bool,
    ]:
        """依次加入直接字段、指标依赖、取值归属、一层外键和参与表主键。"""
        self._add_ranked_resources(ranked)
        self._add_foreign_key_context()
        self._add_primary_keys()
        if self._truncated:
            self._warnings.append(
                "排序后的字段上下文已截断，最多保留 "
                f"{_MAX_RANKED_CONTEXT_COLUMNS} 个资源"
            )
        return (
            self._build_column_results(),
            self._build_table_contexts(),
            self._truncated,
        )

    def _add_ranked_resources(self, ranked: _RankedCandidates) -> None:
        """添加直接字段、指标依赖字段和值所属字段。"""
        for key, rank_score, match_reasons in ranked.columns:
            self._add_column(key, "direct_match", rank_score, match_reasons)
        for metric_name, _, _ in ranked.metrics:
            for reference in self._catalog.metrics[metric_name].relevant_columns:
                self._add_column(
                    column_reference_key(reference),
                    "metric_dependency",
                )
        for (t_name, c_name, _), _, _ in ranked.values:
            self._add_column(
                (t_name, c_name),
                "value_owner",
                counts_toward_limit=False,
            )

    def _add_foreign_key_context(self) -> None:
        """按参与表快照补充一层外键和目标字段，不递归展开新加入的表。"""
        participating_tables = self._participating_tables()
        foreign_keys = sorted(
            (
                column_info
                for column_info in self._catalog.columns.values()
                if column_info.t_name in participating_tables
            ),
            key=lambda column_info: (column_info.t_name, column_info.name),
        )
        for foreign_key in foreign_keys:
            target_t_name = foreign_key.reference_t_name
            target_c_name = foreign_key.reference_c_name
            if not target_t_name or not target_c_name:
                continue
            self._add_column(
                (foreign_key.t_name, foreign_key.name),
                "foreign_key",
                counts_toward_limit=False,
            )
            self._add_column(
                (target_t_name, target_c_name),
                "reference_target",
                counts_toward_limit=False,
            )

    def _add_primary_keys(self) -> None:
        """为参与结果的表补充主键字段。"""
        for t_name in sorted(self._participating_tables()):
            table_info = self._catalog.tables.get(t_name)
            if table_info is None:
                continue
            for primary_key in table_info.primary_key_columns:
                self._add_column(
                    (t_name, primary_key),
                    "primary_key",
                    counts_toward_limit=False,
                )

    def _add_column(
        self,
        key: ColumnKey,
        inclusion_reason: str,
        rank_score: float | None = None,
        match_reasons: list[SemanticMatchReason] | None = None,
        *,
        counts_toward_limit: bool = True,
    ) -> None:
        """添加字段并合并引入原因，仅受限类别计入截断数量。"""
        column_info = self._catalog.columns.get(key)
        if column_info is None:
            return
        existing = self._contexts.get(key)
        if existing is not None:
            if inclusion_reason not in existing.inclusion_reasons:
                existing.inclusion_reasons.append(inclusion_reason)
            return
        if (
            counts_toward_limit
            and self._ranked_context_count >= _MAX_RANKED_CONTEXT_COLUMNS
        ):
            self._truncated = True
            return
        self._contexts[key] = _ColumnContext(
            info=column_info,
            inclusion_reasons=[inclusion_reason],
            rank_score=rank_score,
            match_reasons=match_reasons or [],
        )
        if counts_toward_limit:
            self._ranked_context_count += 1

    def _participating_tables(self) -> set[str]:
        """返回当前字段上下文涉及的表。"""
        return {t_name for t_name, _ in self._contexts}

    def _build_column_results(self) -> list[SemanticColumnRecallResult]:
        """将字段上下文转换为响应模型。"""
        results: list[SemanticColumnRecallResult] = []
        for context in self._contexts.values():
            column_info = context.info
            index_status = _index_status(column_info)
            if index_status != "current" and context.match_reasons:
                self._warnings.append(
                    "字段语义索引状态为 "
                    f"{index_status}: {column_info.t_name}.{column_info.name}"
                )
            results.append(
                SemanticColumnRecallResult(
                    t_name=column_info.t_name,
                    name=column_info.name,
                    type=column_info.type,
                    description=column_info.description,
                    alias=column_info.alias,
                    examples=column_info.examples[:_COLUMN_EXAMPLE_LIMIT],
                    reference_t_name=column_info.reference_t_name,
                    reference_c_name=column_info.reference_c_name,
                    inclusion_reasons=context.inclusion_reasons,
                    rank_score=context.rank_score,
                    match_reasons=context.match_reasons,
                    meta_version=column_info.meta_version,
                    index_version=column_info.index_version,
                    index_status=index_status,
                )
            )
        return results

    def _build_table_contexts(self) -> list[SemanticTableContext]:
        """根据最终字段集合构建表上下文。"""
        return [
            SemanticTableContext(
                name=table_info.name,
                role=table_info.role,
                description=table_info.description,
                primary_key_columns=table_info.primary_key_columns,
                meta_version=table_info.meta_version,
            )
            for t_name in sorted(self._participating_tables())
            if (table_info := self._catalog.tables.get(t_name)) is not None
        ]


def _index_status(item: ColumnInfo | MetricInfo) -> SemanticIndexStatus:
    """根据元数据和索引版本判断索引状态。"""
    if item.index_version <= 0:
        return "missing"
    if item.index_version < item.meta_version:
        return "stale"
    return "current"
