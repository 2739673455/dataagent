"""确定性的元数据语义搜索服务"""

import asyncio
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Literal, TypeVar, cast

from loguru import logger

from app.clients.embedding_client_manager import EmbeddingClient
from app.entities.meta import (
    ColumnInfo,
    ColumnKey,
    MetricInfo,
    TableInfo,
    serialize_column_examples,
)
from app.entities.semantic_search import (
    SearchHit,
    SemanticColumnResult,
    SemanticIndexStatus,
    SemanticMatchReason,
    SemanticMetricResult,
    SemanticRelation,
    SemanticResourceType,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticTableContext,
    SemanticValueResult,
)
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.metric_es_repo import MetricESRepo
from app.repositories.value_es_repo import ValueESRepo
from app.services.authorization_service import AssetAccessPolicy
from app.services.metadata_authorization_filter import MetadataAuthorizationFilter

_RRF_K = 60
_INDEX_SEARCH_LIMIT_MULTIPLIER = 3
_MAX_RANKED_CONTEXT_COLUMNS = 30
_COLUMN_EXAMPLE_LIMIT = 3
_DEFAULT_INDEX_QUERY_CONCURRENCY = 8

CandidateKeyT = TypeVar("CandidateKeyT")
IndexResultT = TypeVar("IndexResultT")
ValueKey = tuple[str, str, str]
ValueSyncStatus = Literal["syncing", "succeeded", "failed"]


def _index_status(item: ColumnInfo | MetricInfo) -> SemanticIndexStatus:
    """根据元数据和索引版本判断索引状态"""
    if item.index_version <= 0:
        return "missing"
    if item.index_version < item.meta_version:
        return "stale"
    return "current"


def _has_semantic_index_match(
    match_reasons: list[SemanticMatchReason],
) -> bool:
    """判断候选是否来自全文或向量语义索引"""
    return any(reason.match_type in {"fulltext", "vector"} for reason in match_reasons)


@dataclass(slots=True)
class _CandidateScore:
    """候选资源的融合分数和命中依据"""

    score: float = 0.0
    reasons: list[SemanticMatchReason] = field(default_factory=list)

    def add(self, score: float, reason: SemanticMatchReason) -> None:
        """累计分数并稳定去重命中依据"""
        self.score += score
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass(slots=True)
class _ColumnContext:
    """待返回字段及其引入原因"""

    info: ColumnInfo
    inclusion_reasons: list[str]
    rank_score: float | None = None
    match_reasons: list[SemanticMatchReason] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        """稳定去重字段引入原因"""
        if reason not in self.inclusion_reasons:
            self.inclusion_reasons.append(reason)


@dataclass(frozen=True, slots=True)
class _SemanticCatalog:
    """语义检索使用的完整元数据目录"""

    tables: dict[str, TableInfo]
    columns: dict[ColumnKey, ColumnInfo]
    metrics: dict[str, MetricInfo]


@dataclass(slots=True)
class _SearchContext:
    """单次语义检索的输入、目录和可变召回状态"""

    request: SemanticSearchRequest
    queries: list[str]
    resource_types: set[SemanticResourceType]
    catalog: _SemanticCatalog
    allowed_columns: frozenset[ColumnKey] | None
    allowed_metrics: frozenset[str] | None
    column_scores: dict[ColumnKey, _CandidateScore] = field(default_factory=dict)
    metric_scores: dict[str, _CandidateScore] = field(default_factory=dict)
    value_scores: dict[ValueKey, _CandidateScore] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    partial: bool = False

    @property
    def search_limit(self) -> int:
        """计算索引层候选扩召数量"""
        return min(
            60,
            self.request.limit_per_type * _INDEX_SEARCH_LIMIT_MULTIPLIER,
        )

    def selects_any(self, *resource_types: SemanticResourceType) -> bool:
        """判断本次请求是否选择任一资源类型"""
        return not self.resource_types.isdisjoint(resource_types)

    def record_backend_failure(
        self,
        backend_name: str,
        error: BaseException,
    ) -> None:
        """记录后端降级并保留任务取消语义"""
        if isinstance(error, asyncio.CancelledError):
            raise error
        if not isinstance(error, Exception):
            raise error
        self.partial = True
        logger.opt(exception=error).warning(
            f"Semantic backend unavailable: {backend_name}"
        )
        warning = f"{backend_name} retrieval unavailable"
        if warning not in self.warnings:
            self.warnings.append(warning)


@dataclass(frozen=True, slots=True)
class _RankedCandidates:
    """三类资源的融合排名结果"""

    columns: list[tuple[ColumnKey, float, list[SemanticMatchReason]]]
    metrics: list[tuple[str, float, list[SemanticMatchReason]]]
    values: list[tuple[ValueKey, float, list[SemanticMatchReason]]]
    truncated: bool


class _ColumnContextBuilder:
    """根据融合候选构建字段、表和一层主外键上下文"""

    def __init__(
        self,
        catalog: _SemanticCatalog,
        warnings: list[str],
    ) -> None:
        self._catalog = catalog
        self._warnings = warnings
        self._contexts: dict[ColumnKey, _ColumnContext] = {}
        self._ranked_context_count = 0
        self._truncated = False

    def build(
        self,
        ranked: _RankedCandidates,
    ) -> tuple[
        list[SemanticColumnResult],
        list[SemanticTableContext],
        list[SemanticRelation],
        bool,
    ]:
        """按直接候选、依赖字段和表关系顺序构建上下文"""
        self._add_ranked_resources(ranked)
        relations = self._build_relations()
        self._add_primary_keys()
        if self._truncated:
            self._warnings.append(
                "Ranked column context truncated at "
                f"{_MAX_RANKED_CONTEXT_COLUMNS} resources"
            )
        return (
            self._build_column_results(),
            self._build_table_contexts(),
            relations,
            self._truncated,
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
        """添加字段上下文并合并引入原因"""
        column_info = self._catalog.columns.get(key)
        if column_info is None:
            return
        existing = self._contexts.get(key)
        if existing is not None:
            existing.add_reason(inclusion_reason)
            if rank_score is not None:
                existing.rank_score = rank_score
                existing.match_reasons = match_reasons or []
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

    def _add_ranked_resources(self, ranked: _RankedCandidates) -> None:
        """添加直接字段、指标依赖字段和值所属字段"""
        for key, rank_score, match_reasons in ranked.columns:
            self._add_column(key, "direct_match", rank_score, match_reasons)
        for metric_name, _, _ in ranked.metrics:
            for reference in self._catalog.metrics[metric_name].relevant_columns:
                self._add_column(
                    (reference["t_name"], reference["c_name"]),
                    "metric_dependency",
                )
        for (t_name, c_name, _), _, _ in ranked.values:
            self._add_column((t_name, c_name), "value_owner")

    def _add_primary_keys(self) -> None:
        """为参与结果的表补充主键字段"""
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

    def _build_relations(self) -> list[SemanticRelation]:
        """补充参与表的一层外键字段并构建关系"""
        relations: dict[tuple[str, str, str, str], SemanticRelation] = {}
        participating_tables = self._participating_tables()
        foreign_keys = sorted(
            (
                column_info
                for column_info in self._catalog.columns.values()
                if column_info.t_name in participating_tables
                and column_info.reference_t_name
                and column_info.reference_c_name
            ),
            key=lambda column_info: (column_info.t_name, column_info.name),
        )
        for foreign_key in foreign_keys:
            target_t_name = cast(str, foreign_key.reference_t_name)
            target_c_name = cast(str, foreign_key.reference_c_name)
            source_key = (foreign_key.t_name, foreign_key.name)
            target_key = (target_t_name, target_c_name)
            self._add_column(
                source_key,
                "foreign_key",
                counts_toward_limit=False,
            )
            self._add_column(
                target_key,
                "reference_target",
                counts_toward_limit=False,
            )
            if source_key not in self._contexts or target_key not in self._contexts:
                continue
            relation_key = (
                source_key[0],
                source_key[1],
                target_key[0],
                target_key[1],
            )
            relations[relation_key] = SemanticRelation(
                source_t_name=source_key[0],
                source_c_name=source_key[1],
                target_t_name=target_key[0],
                target_c_name=target_key[1],
            )
        return list(relations.values())

    def _build_column_results(self) -> list[SemanticColumnResult]:
        """将字段上下文转换为响应模型"""
        results: list[SemanticColumnResult] = []
        for context in self._contexts.values():
            column_info = context.info
            index_status = _index_status(column_info)
            if index_status != "current" and _has_semantic_index_match(
                context.match_reasons
            ):
                self._warnings.append(
                    "Column semantic index is "
                    f"{index_status}: {column_info.t_name}.{column_info.name}"
                )
            results.append(
                SemanticColumnResult(
                    t_name=column_info.t_name,
                    name=column_info.name,
                    type=column_info.type,
                    description=column_info.description,
                    alias=column_info.alias,
                    examples=serialize_column_examples(column_info.examples)[
                        :_COLUMN_EXAMPLE_LIMIT
                    ],
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
        """根据最终字段集合构建表上下文"""
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

    def _participating_tables(self) -> set[str]:
        """返回当前字段上下文涉及的表"""
        return {context.info.t_name for context in self._contexts.values()}


class MetaSearchService:
    """聚合元数据、语义索引和字段值索引"""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        column_repo: ColumnESRepo,
        metric_repo: MetricESRepo,
        value_repo: ValueESRepo,
        meta_repo: MetaPGRepo,
        asset_policy: AssetAccessPolicy,
        data_source: str,
        database_name: str,
        max_concurrent_index_queries: int = _DEFAULT_INDEX_QUERY_CONCURRENCY,
    ) -> None:
        """初始化元数据语义搜索服务"""
        if max_concurrent_index_queries <= 0:
            raise ValueError("max_concurrent_index_queries must be positive")
        self._embedding_client = embedding_client
        self._column_repo = column_repo
        self._metric_repo = metric_repo
        self._value_repo = value_repo
        self._meta_repo = meta_repo
        self._authorization_filter = MetadataAuthorizationFilter(
            asset_policy,
            data_source,
            database_name,
        )
        self._index_query_semaphore = asyncio.Semaphore(max_concurrent_index_queries)

    async def search(self, request: SemanticSearchRequest) -> SemanticSearchResponse:
        """按加载目录、执行召回和构建响应三个阶段完成语义检索"""
        context = await self._create_context(request)
        await self._retrieve(context)
        return self._build_response(context)

    async def _create_context(self, request: SemanticSearchRequest) -> _SearchContext:
        """加载完整元数据并创建单次检索上下文"""
        table_infos, column_infos, metric_infos = await asyncio.gather(
            self._meta_repo.list_table_infos(),
            self._meta_repo.list_column_infos(),
            self._meta_repo.list_metric_infos(),
        )
        allowed_column_keys = self._authorization_filter.allowed_column_keys(
            column_infos
        )
        allowed_columns = {
            (item.t_name, item.name): item
            for item in self._authorization_filter.filter_columns(
                column_infos,
                allowed_column_keys,
            )
        }
        visible_tables = {
            item.name: item
            for item in self._authorization_filter.filter_tables(
                table_infos,
                allowed_column_keys,
            )
        }
        allowed_metrics = {
            item.name: item
            for item in self._authorization_filter.filter_metrics(
                metric_infos,
                allowed_column_keys,
            )
        }
        return _SearchContext(
            request=request,
            queries=list(dict.fromkeys([request.query, *request.terms])),
            resource_types=set(request.resource_types),
            catalog=_SemanticCatalog(
                tables=visible_tables,
                columns=allowed_columns,
                metrics=allowed_metrics,
            ),
            allowed_columns=(
                None
                if self._authorization_filter.unrestricted
                else frozenset(allowed_columns)
            ),
            allowed_metrics=(
                None
                if self._authorization_filter.unrestricted
                else frozenset(allowed_metrics)
            ),
        )

    async def _retrieve(self, context: _SearchContext) -> None:
        """按请求类型执行确定顺序的多路召回"""
        if (
            context.selects_any("column")
            and context.catalog.columns
            or context.selects_any("metric")
            and context.catalog.metrics
        ):
            await self._collect_fulltext_matches(context)
            await self._collect_vector_matches(context)
        if context.selects_any("value") and context.catalog.columns:
            await self._collect_value_matches(context)

    async def _collect_fulltext_matches(
        self,
        context: _SearchContext,
    ) -> None:
        """收集字段和指标全文命中"""
        if context.selects_any("column") and context.catalog.columns:
            results = await asyncio.gather(
                *(
                    self._run_index_query(
                        self._column_repo.search_text_hits(
                            query,
                            allowed_columns=context.allowed_columns,
                            limit=context.search_limit,
                        )
                    )
                    for query in context.queries
                ),
                return_exceptions=True,
            )
            self._merge_column_hits(
                context,
                results,
                backend_name="Column full-text",
                match_type="fulltext",
            )

        if context.selects_any("metric") and context.catalog.metrics:
            results = await asyncio.gather(
                *(
                    self._run_index_query(
                        self._metric_repo.search_text_hits(
                            query,
                            allowed_metrics=context.allowed_metrics,
                            limit=context.search_limit,
                        )
                    )
                    for query in context.queries
                ),
                return_exceptions=True,
            )
            self._merge_metric_hits(
                context,
                results,
                backend_name="Metric full-text",
                match_type="fulltext",
            )

    async def _collect_vector_matches(
        self,
        context: _SearchContext,
    ) -> None:
        """收集字段和指标向量命中"""
        try:
            embeddings = await self._embedding_client.aembed_documents(context.queries)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            context.record_backend_failure("Embedding", exc)
            return

        if context.selects_any("column") and context.catalog.columns:
            results = await asyncio.gather(
                *(
                    self._run_index_query(
                        self._column_repo.search_vector_hits(
                            embedding,
                            allowed_columns=context.allowed_columns,
                            limit=context.search_limit,
                        )
                    )
                    for embedding in embeddings
                ),
                return_exceptions=True,
            )
            self._merge_column_hits(
                context,
                results,
                backend_name="Column vector",
                match_type="vector",
            )

        if context.selects_any("metric") and context.catalog.metrics:
            results = await asyncio.gather(
                *(
                    self._run_index_query(
                        self._metric_repo.search_vector_hits(
                            embedding,
                            allowed_metrics=context.allowed_metrics,
                            limit=context.search_limit,
                        )
                    )
                    for embedding in embeddings
                ),
                return_exceptions=True,
            )
            self._merge_metric_hits(
                context,
                results,
                backend_name="Metric vector",
                match_type="vector",
            )

    def _merge_column_hits(
        self,
        context: _SearchContext,
        results: list[list[SearchHit[ColumnInfo]] | BaseException],
        *,
        backend_name: str,
        match_type: Literal["fulltext", "vector"],
    ) -> None:
        """校验并融合每个查询的字段索引命中"""
        for query, result in zip(context.queries, results, strict=True):
            if isinstance(result, BaseException):
                context.record_backend_failure(backend_name, result)
                continue
            seen_keys: set[ColumnKey] = set()
            for rank, hit in enumerate(result, start=1):
                key = (hit.item.t_name, hit.item.name)
                if key not in context.catalog.columns or key in seen_keys:
                    continue
                seen_keys.add(key)
                self._add_candidate_score(
                    context.column_scores,
                    key,
                    self._rrf_score(rank),
                    SemanticMatchReason(
                        match_type=match_type,
                        query=query,
                        score=hit.score,
                    ),
                )

    def _merge_metric_hits(
        self,
        context: _SearchContext,
        results: list[list[SearchHit[MetricInfo]] | BaseException],
        *,
        backend_name: str,
        match_type: Literal["fulltext", "vector"],
    ) -> None:
        """校验并融合每个查询的指标索引命中"""
        for query, result in zip(context.queries, results, strict=True):
            if isinstance(result, BaseException):
                context.record_backend_failure(backend_name, result)
                continue
            seen_names: set[str] = set()
            for rank, hit in enumerate(result, start=1):
                if (
                    hit.item.name not in context.catalog.metrics
                    or hit.item.name in seen_names
                ):
                    continue
                seen_names.add(hit.item.name)
                self._add_candidate_score(
                    context.metric_scores,
                    hit.item.name,
                    self._rrf_score(rank),
                    SemanticMatchReason(
                        match_type=match_type,
                        query=query,
                        score=hit.score,
                    ),
                )

    async def _collect_value_matches(
        self,
        context: _SearchContext,
    ) -> None:
        """收集字段值全文索引命中"""
        results = await asyncio.gather(
            *(
                self._run_index_query(
                    self._value_repo.search_hits(
                        query,
                        allowed_columns=context.allowed_columns,
                        limit=context.search_limit,
                    )
                )
                for query in context.queries
            ),
            return_exceptions=True,
        )
        for query, result in zip(context.queries, results, strict=True):
            if isinstance(result, BaseException):
                context.record_backend_failure("Value full-text", result)
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
                        query=query,
                        score=hit.score,
                    ),
                )

    def _build_response(self, context: _SearchContext) -> SemanticSearchResponse:
        """融合候选排名并组装最终语义检索响应"""
        ranked = self._rank_context(context)
        metric_results = self._build_metric_results(ranked.metrics, context)
        value_results = self._build_value_results(ranked.values, context)
        (
            column_results,
            table_contexts,
            relations,
            context_truncated,
        ) = _ColumnContextBuilder(
            context.catalog,
            context.warnings,
        ).build(ranked)
        return SemanticSearchResponse(
            status="partial" if context.partial else "success",
            search_id=f"search_{uuid.uuid4().hex}",
            queries=context.queries,
            metrics=metric_results,
            columns=column_results,
            values=value_results,
            tables=table_contexts,
            relations=relations,
            warnings=context.warnings,
            truncated=ranked.truncated or context_truncated,
        )

    def _rank_context(self, context: _SearchContext) -> _RankedCandidates:
        """对三类候选执行类型内融合排名"""
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
    def _add_candidate_score(
        scores: dict[CandidateKeyT, _CandidateScore],
        key: CandidateKeyT,
        score: float,
        reason: SemanticMatchReason,
    ) -> None:
        """新增或合并候选资源分数"""
        scores.setdefault(key, _CandidateScore()).add(score, reason)

    @staticmethod
    def _rank_candidates(
        scores: dict[CandidateKeyT, _CandidateScore],
        limit: int,
    ) -> tuple[
        list[tuple[CandidateKeyT, float, list[SemanticMatchReason]]],
        bool,
    ]:
        """按融合分数排序并归一化为类型内排名分数"""
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
        context: _SearchContext,
    ) -> list[SemanticMetricResult]:
        """构建指标检索响应"""
        results: list[SemanticMetricResult] = []
        for name, rank_score, match_reasons in ranked_metrics:
            metric_info = context.catalog.metrics[name]
            index_status = _index_status(metric_info)
            if index_status != "current" and _has_semantic_index_match(match_reasons):
                context.warnings.append(
                    f"Metric semantic index is {index_status}: {name}"
                )
            results.append(
                SemanticMetricResult(
                    name=metric_info.name,
                    description=metric_info.description,
                    alias=metric_info.alias,
                    relevant_columns=[
                        {
                            "t_name": reference["t_name"],
                            "c_name": reference["c_name"],
                        }
                        for reference in metric_info.relevant_columns
                    ],
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
        context: _SearchContext,
    ) -> list[SemanticValueResult]:
        """构建字段值检索响应"""
        results: list[SemanticValueResult] = []
        warned_columns: set[ColumnKey] = set()
        for (t_name, c_name, value), rank_score, match_reasons in ranked_values:
            column_info = context.catalog.columns[(t_name, c_name)]
            sync_status = self._value_sync_status(column_info.value_index_sync_status)
            if sync_status != "succeeded" and (t_name, c_name) not in warned_columns:
                context.warnings.append(
                    f"Value index status is {sync_status or 'unknown'}: "
                    f"{t_name}.{c_name}"
                )
                warned_columns.add((t_name, c_name))
            results.append(
                SemanticValueResult(
                    value=value,
                    t_name=t_name,
                    c_name=c_name,
                    rank_score=rank_score,
                    match_reasons=match_reasons,
                    sync_status=sync_status,
                    synced_at=column_info.value_index_synced_at,
                )
            )
        return results

    @staticmethod
    def _rrf_score(rank: int) -> float:
        """计算倒数排名融合分数"""
        return 1 / (_RRF_K + rank)

    async def _run_index_query(
        self,
        operation: Awaitable[IndexResultT],
    ) -> IndexResultT:
        """限制当前服务实例的索引查询并发量"""
        async with self._index_query_semaphore:
            return await operation

    @staticmethod
    def _value_sync_status(status: str | None) -> ValueSyncStatus | None:
        """将数据库字段值同步状态收窄到响应枚举"""
        if status in {"syncing", "succeeded", "failed"}:
            return cast(ValueSyncStatus, status)
        return None
