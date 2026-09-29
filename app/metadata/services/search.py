"""确定性的元数据语义资源召回服务。"""

import asyncio
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from loguru import logger

from app.identity.models.authorization import AssetAccessPolicy
from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnKey,
    MetricInfo,
    TableInfo,
    column_reference_key,
)
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticRecallFailure,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticResourceType,
    SemanticTableContext,
    SemanticValueRecallResult,
)
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.semantic_index import SemanticIndexRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.contracts.search import SearchHit
from app.shared.database.base import MetaBase

_RRF_K = 60
_INDEX_SEARCH_LIMIT_MULTIPLIER = 3
_MAX_RANKED_CONTEXT_COLUMNS = 30
_COLUMN_EXAMPLE_LIMIT = 3
_DEFAULT_INDEX_QUERY_CONCURRENCY = 8

CandidateItemT = TypeVar("CandidateItemT", bound=MetaBase)
CandidateKeyT = TypeVar("CandidateKeyT")
IndexResultT = TypeVar("IndexResultT")
ValueKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class SemanticCatalog:
    """语义召回使用的完整元数据目录。"""

    tables: dict[str, TableInfo]
    columns: dict[ColumnKey, ColumnInfo]
    metrics: dict[str, MetricInfo]


@dataclass(slots=True)
class RecallContext:
    """单次语义召回的输入、目录和可变状态。"""

    request: SemanticResourceRecallRequest
    catalog: SemanticCatalog
    column_scores: dict[ColumnKey, float] = field(default_factory=dict)
    metric_scores: dict[str, float] = field(default_factory=dict)
    value_scores: dict[ValueKey, float] = field(default_factory=dict)
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
class RankedCandidates:
    """三类资源的融合排名结果。"""

    columns: list[tuple[ColumnKey, float]]
    metrics: list[tuple[str, float]]
    values: list[tuple[ValueKey, float]]
    truncated: bool


@dataclass(slots=True)
class ColumnContext:
    """待返回字段及其引入原因。"""

    info: ColumnInfo
    inclusion_reasons: list[str]
    rank_score: float | None = None


class ColumnContextBuilder:
    """合并候选字段，补充主键与直接外键引用，并构建表上下文。"""

    def __init__(
        self,
        catalog: SemanticCatalog,
        warnings: list[str],
    ) -> None:
        """初始化语义目录、告警集合和字段上下文缓存。"""
        self._catalog = catalog
        self._warnings = warnings
        self._contexts: dict[ColumnKey, ColumnContext] = {}
        self._ranked_context_count = 0
        self._truncated = False

    def build(
        self,
        ranked: RankedCandidates,
    ) -> tuple[
        list[SemanticColumnRecallResult],
        list[SemanticTableContext],
        bool,
    ]:
        """依次合并候选、补充直接外键和主键，再构建字段与表响应。"""
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

    def _add_ranked_resources(self, ranked: RankedCandidates) -> None:
        """添加直接字段、指标依赖字段和值所属字段。"""
        for key, rank_score in ranked.columns:
            self._add_column(key, "direct_match", rank_score)
        for metric_name, _ in ranked.metrics:
            for reference in self._catalog.metrics[metric_name].relevant_columns:
                self._add_column(
                    column_reference_key(reference),
                    "metric_dependency",
                )
        for (t_name, c_name, _), _ in ranked.values:
            self._add_column(
                (t_name, c_name),
                "value_owner",
                counts_toward_limit=False,
            )

    def _add_foreign_key_context(self) -> None:
        """补充参与表的外键及其直接引用目标，不递归展开。"""
        participating_tables = self._participating_tables()
        for column in sorted(
            (
                column_info
                for column_info in self._catalog.columns.values()
                if column_info.t_name in participating_tables
            ),
            key=lambda column_info: (column_info.t_name, column_info.name),
        ):
            target_table = column.reference_t_name
            target_column = column.reference_c_name
            if not target_table or not target_column:
                continue
            self._add_column(
                (column.t_name, column.name),
                "foreign_key",
                counts_toward_limit=False,
            )
            self._add_column(
                (target_table, target_column),
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
        *,
        counts_toward_limit: bool = True,
    ) -> None:
        """新增字段或合并原因，仅直接命中及指标依赖占用截断名额。"""
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
        self._contexts[key] = ColumnContext(
            info=column_info,
            inclusion_reasons=[inclusion_reason],
            rank_score=rank_score,
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
            )
            for t_name in sorted(self._participating_tables())
            if (table_info := self._catalog.tables.get(t_name)) is not None
        ]


class SemanticResourceRecallService:
    """聚合元数据、语义索引和字段值索引。"""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        column_repo: ColumnESRepo,
        metric_repo: MetricESRepo,
        value_repo: ValueESRepo,
        catalog: SemanticCatalog,
        asset_policy: AssetAccessPolicy,
        data_source: str,
        database_name: str,
        max_concurrent_index_queries: int = _DEFAULT_INDEX_QUERY_CONCURRENCY,
    ) -> None:
        """初始化元数据语义资源召回服务。"""
        self._embedding_client = embedding_client
        self._column_repo = column_repo
        self._metric_repo = metric_repo
        self._value_repo = value_repo
        self._catalog = catalog
        self._authorization_filter = MetadataAuthorizationFilter(
            asset_policy,
            data_source,
            database_name,
        )
        self._index_query_semaphore = asyncio.Semaphore(max_concurrent_index_queries)

    async def recall(
        self,
        request: SemanticResourceRecallRequest,
    ) -> SemanticResourceRecallResponse:
        """按目录权限过滤、外部检索和响应构建三个阶段完成召回。"""
        context = self._create_context(request)
        await self._retrieve(context)
        return self._build_response(context)

    def _create_context(
        self,
        request: SemanticResourceRecallRequest,
    ) -> RecallContext:
        """按权限过滤已加载的目录并创建单次检索上下文。"""
        table_infos = list(self._catalog.tables.values())
        column_infos = list(self._catalog.columns.values())
        metric_infos = list(self._catalog.metrics.values())
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
        return RecallContext(
            request=request,
            catalog=SemanticCatalog(
                tables=visible_tables,
                columns=allowed_columns,
                metrics=allowed_metrics,
            ),
        )

    async def _retrieve(self, context: RecallContext) -> None:
        """按资源选择检索，共享一次查询向量生成。"""
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
            await self._collect_column_matches(context, embeddings)
        if search_metrics:
            await self._collect_metric_matches(context, embeddings)
        if "value" in context.request.resource_types and context.catalog.columns:
            await self._collect_value_matches(context)

    async def _collect_column_matches(
        self,
        context: RecallContext,
        embeddings: list[list[float]] | None,
    ) -> None:
        """收集并融合字段的全文和向量命中。"""
        await self._collect_semantic_matches(
            context,
            embeddings,
            self._column_repo,
            resource_type="column",
            allowed_keys=frozenset(context.catalog.columns),
            scores=context.column_scores,
            key_of=lambda item: (item.t_name, item.name),
            backend_name="字段",
        )

    async def _collect_metric_matches(
        self,
        context: RecallContext,
        embeddings: list[list[float]] | None,
    ) -> None:
        """收集并融合指标的全文和向量命中。"""
        await self._collect_semantic_matches(
            context,
            embeddings,
            self._metric_repo,
            resource_type="metric",
            allowed_keys=frozenset(context.catalog.metrics),
            scores=context.metric_scores,
            key_of=lambda item: item.name,
            backend_name="指标",
        )

    async def _collect_semantic_matches(
        self,
        context: RecallContext,
        embeddings: list[list[float]] | None,
        repo: SemanticIndexRepo[CandidateItemT, CandidateKeyT],
        *,
        resource_type: Literal["column", "metric"],
        allowed_keys: frozenset[CandidateKeyT],
        scores: dict[CandidateKeyT, float],
        key_of: Callable[[CandidateItemT], CandidateKeyT],
        backend_name: str,
    ) -> None:
        """共用批量查询与排名融合，各通道失败独立记录。"""
        for channel in ("fulltext", "vector"):
            if channel == "fulltext":
                operations = [
                    repo.search_text_hits(
                        term, allowed_keys=allowed_keys, limit=context.search_limit
                    )
                    for term in context.request.terms
                ]
            elif embeddings is not None:
                operations = [
                    repo.search_vector_hits(
                        embedding, allowed_keys=allowed_keys, limit=context.search_limit
                    )
                    for embedding in embeddings
                ]
            else:
                continue
            results = await asyncio.gather(
                *(self._run_index_query(operation) for operation in operations),
                return_exceptions=True,
            )
            self._merge_hits(
                context,
                results,
                resource_type=resource_type,
                allowed_keys=allowed_keys,
                scores=scores,
                key_of=key_of,
                backend_name=backend_name
                + ("全文" if channel == "fulltext" else "向量"),
                match_type=channel,
            )

    async def _collect_value_matches(
        self,
        context: RecallContext,
    ) -> None:
        """检索字段取值，并按各检索词的排名累加融合分数。"""
        allowed_columns = frozenset(context.catalog.columns)
        results = await asyncio.gather(
            *(
                self._run_index_query(
                    self._value_repo.search_hits(
                        term,
                        allowed_columns=allowed_columns,
                        limit=context.search_limit,
                    )
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
                    rank,
                )

    async def _run_index_query(
        self,
        operation: Awaitable[IndexResultT],
    ) -> IndexResultT:
        """限制当前服务实例的索引查询并发量。"""
        async with self._index_query_semaphore:
            return await operation

    def _merge_hits(
        self,
        context: RecallContext,
        results: list[list[SearchHit[CandidateItemT]] | BaseException],
        *,
        resource_type: Literal["column", "metric"],
        allowed_keys: Collection[CandidateKeyT],
        scores: dict[CandidateKeyT, float],
        key_of: Callable[[CandidateItemT], CandidateKeyT],
        backend_name: str,
        match_type: Literal["fulltext", "vector"],
    ) -> None:
        """按目录过滤命中，每个检索词内去重后按原始排名累加分数。"""
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
            seen_keys: set[CandidateKeyT] = set()
            for rank, hit in enumerate(result, start=1):
                key = key_of(hit.item)
                if key not in allowed_keys or key in seen_keys:
                    continue
                seen_keys.add(key)
                self._add_candidate_score(scores, key, rank)

    @staticmethod
    def _add_candidate_score(
        scores: dict[CandidateKeyT, float],
        key: CandidateKeyT,
        rank: int,
    ) -> None:
        """按候选排名累加倒数排名融合分数。"""
        scores[key] = scores.get(key, 0.0) + 1 / (_RRF_K + rank)

    def _build_response(
        self,
        context: RecallContext,
    ) -> SemanticResourceRecallResponse:
        """融合候选排名并组装最终语义召回响应。"""
        ranked = self._rank_context(context)
        metric_results = self._build_metric_results(ranked.metrics, context)
        value_results = self._build_value_results(ranked.values)
        (
            column_results,
            table_contexts,
            context_truncated,
        ) = ColumnContextBuilder(
            context.catalog,
            context.warnings,
        ).build(ranked)
        return SemanticResourceRecallResponse(
            status="partial" if context.failures else "success",
            terms=context.request.terms,
            metrics=metric_results,
            columns=column_results,
            values=value_results,
            tables=table_contexts,
            failures=context.failures,
            warnings=context.warnings,
            truncated=ranked.truncated or context_truncated,
        )

    def _rank_context(self, context: RecallContext) -> RankedCandidates:
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
        return RankedCandidates(
            columns=columns,
            metrics=metrics,
            values=values,
            truncated=(columns_truncated or metrics_truncated or values_truncated),
        )

    @staticmethod
    def _rank_candidates(
        scores: dict[CandidateKeyT, float],
        limit: int,
    ) -> tuple[
        list[tuple[CandidateKeyT, float]],
        bool,
    ]:
        """按融合分数排序、截断，以类型内最高分归一化；分数不是概率。"""
        ordered = sorted(
            scores.items(),
            key=lambda item: (-item[1], str(item[0])),
        )
        if not ordered:
            return [], False
        max_score = ordered[0][1]
        ranked = [
            (
                key,
                round(score / max_score, 6),
            )
            for key, score in ordered[:limit]
        ]
        return ranked, len(ordered) > limit

    def _build_metric_results(
        self,
        ranked_metrics: list[tuple[str, float]],
        context: RecallContext,
    ) -> list[SemanticMetricRecallResult]:
        """构建指标检索响应。"""
        results: list[SemanticMetricRecallResult] = []
        for name, rank_score in ranked_metrics:
            metric_info = context.catalog.metrics[name]
            results.append(
                SemanticMetricRecallResult(
                    name=metric_info.name,
                    description=metric_info.description,
                    alias=metric_info.alias,
                    relevant_columns=metric_info.relevant_columns,
                    rank_score=rank_score,
                )
            )
        return results

    @staticmethod
    def _build_value_results(
        ranked_values: list[tuple[ValueKey, float]],
    ) -> list[SemanticValueRecallResult]:
        """构建字段值检索响应。"""
        return [
            SemanticValueRecallResult(
                value=value,
                t_name=t_name,
                c_name=c_name,
                rank_score=rank_score,
            )
            for (t_name, c_name, value), rank_score in ranked_values
        ]
