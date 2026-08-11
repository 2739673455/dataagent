"""确定性的语义目录检索服务"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Literal, TypeVar, cast

from app.clients.embedding_client_manager import EmbeddingClient
from app.entities.meta import (
    ColumnInfo,
    ColumnKey,
    MetricInfo,
    TableInfo,
    serialize_column_examples,
)
from app.entities.semantic_search import (
    SemanticColumnResult,
    SemanticIndexStatus,
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

_RRF_K = 60
_INDEX_SEARCH_LIMIT_MULTIPLIER = 3
_MAX_CONTEXT_COLUMNS = 30
_COLUMN_EXAMPLE_LIMIT = 3

CandidateKeyT = TypeVar("CandidateKeyT")
ValueKey = tuple[str, str, str]
ValueSyncStatus = Literal["syncing", "succeeded", "failed"]


@dataclass(slots=True)
class _CandidateScore:
    """候选资源的融合分数和命中依据"""

    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, score: float, reason: str) -> None:
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
    match_reasons: list[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        """稳定去重字段引入原因"""
        if reason not in self.inclusion_reasons:
            self.inclusion_reasons.append(reason)


class SemanticCatalogService:
    """聚合元数据、语义索引和字段值索引"""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        column_repo: ColumnESRepo,
        metric_repo: MetricESRepo,
        value_repo: ValueESRepo,
        meta_repo: MetaPGRepo,
    ) -> None:
        """初始化语义目录检索服务"""
        self._embedding_client = embedding_client
        self._column_repo = column_repo
        self._metric_repo = metric_repo
        self._value_repo = value_repo
        self._meta_repo = meta_repo

    async def search(self, request: SemanticSearchRequest) -> SemanticSearchResponse:
        """检索语义资源并组装受控范围的关系上下文"""
        queries = list(dict.fromkeys([request.query, *request.terms]))
        warnings: list[str] = []
        partial = False

        table_infos = await self._meta_repo.list_table_infos()
        column_infos = await self._meta_repo.list_column_infos()
        metric_infos = await self._meta_repo.list_metric_infos()

        all_tables = {table_info.name: table_info for table_info in table_infos}
        requested_tables = set(request.table_names)
        allowed_tables = (
            requested_tables & all_tables.keys()
            if requested_tables
            else set(all_tables)
        )
        unknown_tables = requested_tables - all_tables.keys()
        if unknown_tables:
            warnings.append(
                "Unknown table scopes: " + ", ".join(sorted(unknown_tables))
            )

        columns = {
            (column_info.t_name, column_info.name): column_info
            for column_info in column_infos
            if column_info.t_name in allowed_tables
        }
        metrics = {
            metric_info.name: metric_info
            for metric_info in metric_infos
            if self._metric_is_in_scope(
                metric_info,
                allowed_tables,
                scope_requested=bool(requested_tables),
            )
        }

        column_scores: dict[ColumnKey, _CandidateScore] = {}
        metric_scores: dict[str, _CandidateScore] = {}
        value_scores: dict[ValueKey, _CandidateScore] = {}
        resource_types = set(request.resource_types)

        if resource_types & {"column", "metric"}:
            fulltext_partial = await self._collect_fulltext_matches(
                queries,
                request.limit_per_type,
                resource_types,
                columns,
                metrics,
                column_scores,
                metric_scores,
                warnings,
                table_names=(sorted(allowed_tables) if requested_tables else None),
            )
            partial = partial or fulltext_partial

        if resource_types & {"column", "metric"}:
            vector_partial = await self._collect_vector_matches(
                queries,
                request.limit_per_type,
                resource_types,
                columns,
                metrics,
                column_scores,
                metric_scores,
                warnings,
                table_names=(sorted(allowed_tables) if requested_tables else None),
            )
            partial = partial or vector_partial

        if "value" in resource_types:
            value_partial = await self._collect_value_matches(
                queries,
                request.limit_per_type,
                columns,
                value_scores,
                warnings,
                table_names=(sorted(allowed_tables) if requested_tables else None),
            )
            partial = partial or value_partial

        ranked_columns, columns_truncated = self._rank_candidates(
            column_scores, request.limit_per_type
        )
        ranked_metrics, metrics_truncated = self._rank_candidates(
            metric_scores, request.limit_per_type
        )
        ranked_values, values_truncated = self._rank_candidates(
            value_scores, request.limit_per_type
        )

        metric_results = self._build_metric_results(ranked_metrics, metrics, warnings)
        value_results = self._build_value_results(ranked_values, columns, warnings)
        (
            column_results,
            table_contexts,
            relations,
            context_truncated,
        ) = self._build_column_context(
            ranked_columns,
            ranked_metrics,
            ranked_values,
            columns,
            metrics,
            all_tables,
            allowed_tables,
            request.include_relations,
            warnings,
        )

        return SemanticSearchResponse(
            status="partial" if partial else "success",
            search_id=f"search_{uuid.uuid4().hex}",
            queries=queries,
            metrics=metric_results,
            columns=column_results,
            values=value_results,
            tables=table_contexts,
            relations=relations,
            warnings=warnings,
            truncated=(
                columns_truncated
                or metrics_truncated
                or values_truncated
                or context_truncated
            ),
        )

    @staticmethod
    def _metric_is_in_scope(
        metric_info: MetricInfo,
        allowed_tables: set[str],
        *,
        scope_requested: bool,
    ) -> bool:
        """判断指标是否位于请求表范围内"""
        if not scope_requested:
            return True
        return any(
            reference["t_name"] in allowed_tables
            for reference in metric_info.relevant_columns
        )

    async def _collect_fulltext_matches(
        self,
        queries: list[str],
        limit: int,
        resource_types: set[SemanticResourceType],
        columns: dict[ColumnKey, ColumnInfo],
        metrics: dict[str, MetricInfo],
        column_scores: dict[ColumnKey, _CandidateScore],
        metric_scores: dict[str, _CandidateScore],
        warnings: list[str],
        table_names: list[str] | None,
    ) -> bool:
        """收集字段和指标全文命中"""
        search_limit = min(60, limit * _INDEX_SEARCH_LIMIT_MULTIPLIER)
        partial = False

        if "column" in resource_types:
            results = await asyncio.gather(
                *(
                    self._column_repo.search_text_hits(
                        query,
                        limit=search_limit,
                        table_names=table_names,
                    )
                    for query in queries
                ),
                return_exceptions=True,
            )
            for query, result in zip(queries, results, strict=True):
                if isinstance(result, BaseException):
                    self._raise_if_cancelled(result)
                    partial = True
                    self._append_backend_warning(
                        warnings,
                        "Column full-text",
                        result,
                    )
                    continue
                seen_keys: set[ColumnKey] = set()
                for rank, hit in enumerate(result, start=1):
                    key = (hit.item.t_name, hit.item.name)
                    if key not in columns or key in seen_keys:
                        continue
                    seen_keys.add(key)
                    self._add_candidate_score(
                        column_scores,
                        key,
                        self._rrf_score(rank),
                        f"fulltext:{query}:{hit.score:.4f}",
                    )

        if "metric" in resource_types:
            results = await asyncio.gather(
                *(
                    self._metric_repo.search_text_hits(
                        query,
                        limit=search_limit,
                    )
                    for query in queries
                ),
                return_exceptions=True,
            )
            for query, result in zip(queries, results, strict=True):
                if isinstance(result, BaseException):
                    self._raise_if_cancelled(result)
                    partial = True
                    self._append_backend_warning(
                        warnings,
                        "Metric full-text",
                        result,
                    )
                    continue
                seen_names: set[str] = set()
                for rank, hit in enumerate(result, start=1):
                    if hit.item.name not in metrics or hit.item.name in seen_names:
                        continue
                    seen_names.add(hit.item.name)
                    self._add_candidate_score(
                        metric_scores,
                        hit.item.name,
                        self._rrf_score(rank),
                        f"fulltext:{query}:{hit.score:.4f}",
                    )

        return partial

    async def _collect_vector_matches(
        self,
        queries: list[str],
        limit: int,
        resource_types: set[SemanticResourceType],
        columns: dict[ColumnKey, ColumnInfo],
        metrics: dict[str, MetricInfo],
        column_scores: dict[ColumnKey, _CandidateScore],
        metric_scores: dict[str, _CandidateScore],
        warnings: list[str],
        table_names: list[str] | None,
    ) -> bool:
        """收集字段和指标向量命中"""
        embedding_results = await asyncio.gather(
            self._embedding_client.aembed_documents(queries),
            return_exceptions=True,
        )
        embedding_result = embedding_results[0]
        if isinstance(embedding_result, BaseException):
            self._raise_if_cancelled(embedding_result)
            warnings.append(
                "Embedding retrieval unavailable: "
                + self._format_exception(embedding_result)
            )
            return True

        embeddings = cast(list[list[float]], embedding_result)
        search_limit = min(60, limit * _INDEX_SEARCH_LIMIT_MULTIPLIER)
        partial = False

        if "column" in resource_types:
            results = await asyncio.gather(
                *(
                    self._column_repo.search_vector_hits(
                        embedding,
                        limit=search_limit,
                        table_names=table_names,
                    )
                    for embedding in embeddings
                ),
                return_exceptions=True,
            )
            for query, result in zip(queries, results, strict=True):
                if isinstance(result, BaseException):
                    self._raise_if_cancelled(result)
                    partial = True
                    self._append_backend_warning(warnings, "Column vector", result)
                    continue
                seen_keys: set[ColumnKey] = set()
                for rank, hit in enumerate(result, start=1):
                    key = (hit.item.t_name, hit.item.name)
                    if key not in columns or key in seen_keys:
                        continue
                    seen_keys.add(key)
                    self._add_candidate_score(
                        column_scores,
                        key,
                        self._rrf_score(rank),
                        f"vector:{query}:{hit.score:.4f}",
                    )

        if "metric" in resource_types:
            results = await asyncio.gather(
                *(
                    self._metric_repo.search_vector_hits(
                        embedding,
                        limit=search_limit,
                    )
                    for embedding in embeddings
                ),
                return_exceptions=True,
            )
            for query, result in zip(queries, results, strict=True):
                if isinstance(result, BaseException):
                    self._raise_if_cancelled(result)
                    partial = True
                    self._append_backend_warning(warnings, "Metric vector", result)
                    continue
                seen_names: set[str] = set()
                for rank, hit in enumerate(result, start=1):
                    if hit.item.name not in metrics or hit.item.name in seen_names:
                        continue
                    seen_names.add(hit.item.name)
                    self._add_candidate_score(
                        metric_scores,
                        hit.item.name,
                        self._rrf_score(rank),
                        f"vector:{query}:{hit.score:.4f}",
                    )

        return partial

    async def _collect_value_matches(
        self,
        queries: list[str],
        limit: int,
        columns: dict[ColumnKey, ColumnInfo],
        scores: dict[ValueKey, _CandidateScore],
        warnings: list[str],
        table_names: list[str] | None,
    ) -> bool:
        """收集字段值全文索引命中"""
        search_limit = min(60, limit * _INDEX_SEARCH_LIMIT_MULTIPLIER)
        results = await asyncio.gather(
            *(
                self._value_repo.search_hits(
                    query,
                    limit=search_limit,
                    table_names=table_names,
                )
                for query in queries
            ),
            return_exceptions=True,
        )
        partial = False
        for query, result in zip(queries, results, strict=True):
            if isinstance(result, BaseException):
                self._raise_if_cancelled(result)
                partial = True
                self._append_backend_warning(warnings, "Value full-text", result)
                continue
            for rank, hit in enumerate(result, start=1):
                column_key = (hit.item.t_name, hit.item.c_name)
                column_info = columns.get(column_key)
                if column_info is None or not column_info.index_values:
                    continue
                key = (hit.item.t_name, hit.item.c_name, hit.item.value)
                self._add_candidate_score(
                    scores,
                    key,
                    self._rrf_score(rank),
                    f"fulltext:{query}:{hit.score:.4f}",
                )
        return partial

    @staticmethod
    def _add_candidate_score(
        scores: dict[CandidateKeyT, _CandidateScore],
        key: CandidateKeyT,
        score: float,
        reason: str,
    ) -> None:
        """新增或合并候选资源分数"""
        scores.setdefault(key, _CandidateScore()).add(score, reason)

    @staticmethod
    def _rank_candidates(
        scores: dict[CandidateKeyT, _CandidateScore],
        limit: int,
    ) -> tuple[list[tuple[CandidateKeyT, float, list[str]]], bool]:
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
        ranked_metrics: list[tuple[str, float, list[str]]],
        metrics: dict[str, MetricInfo],
        warnings: list[str],
    ) -> list[SemanticMetricResult]:
        """构建指标检索响应"""
        results: list[SemanticMetricResult] = []
        for name, rank_score, match_reasons in ranked_metrics:
            metric_info = metrics[name]
            index_status = self._index_status(metric_info)
            if index_status != "current" and self._has_semantic_index_match(
                match_reasons
            ):
                warnings.append(f"Metric semantic index is {index_status}: {name}")
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
        ranked_values: list[tuple[ValueKey, float, list[str]]],
        columns: dict[ColumnKey, ColumnInfo],
        warnings: list[str],
    ) -> list[SemanticValueResult]:
        """构建字段值检索响应"""
        results: list[SemanticValueResult] = []
        warned_columns: set[ColumnKey] = set()
        for (t_name, c_name, value), rank_score, match_reasons in ranked_values:
            column_info = columns[(t_name, c_name)]
            sync_status = self._value_sync_status(column_info.value_index_sync_status)
            if sync_status != "succeeded" and (t_name, c_name) not in warned_columns:
                warnings.append(
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

    def _build_column_context(
        self,
        ranked_columns: list[tuple[ColumnKey, float, list[str]]],
        ranked_metrics: list[tuple[str, float, list[str]]],
        ranked_values: list[tuple[ValueKey, float, list[str]]],
        columns: dict[ColumnKey, ColumnInfo],
        metrics: dict[str, MetricInfo],
        all_tables: dict[str, TableInfo],
        allowed_tables: set[str],
        include_relations: bool,
        warnings: list[str],
    ) -> tuple[
        list[SemanticColumnResult],
        list[SemanticTableContext],
        list[SemanticRelation],
        bool,
    ]:
        """组装直接命中字段和一层关系依赖"""
        contexts: dict[ColumnKey, _ColumnContext] = {}
        context_truncated = False

        def add_column(
            key: ColumnKey,
            inclusion_reason: str,
            rank_score: float | None = None,
            match_reasons: list[str] | None = None,
        ) -> None:
            nonlocal context_truncated
            column_info = columns.get(key)
            if column_info is None:
                return
            existing = contexts.get(key)
            if existing is not None:
                existing.add_reason(inclusion_reason)
                if rank_score is not None:
                    existing.rank_score = rank_score
                    existing.match_reasons = match_reasons or []
                return
            if len(contexts) >= _MAX_CONTEXT_COLUMNS:
                context_truncated = True
                return
            contexts[key] = _ColumnContext(
                info=column_info,
                inclusion_reasons=[inclusion_reason],
                rank_score=rank_score,
                match_reasons=match_reasons or [],
            )

        for key, rank_score, match_reasons in ranked_columns:
            add_column(key, "direct_match", rank_score, match_reasons)

        for metric_name, _, _ in ranked_metrics:
            for reference in metrics[metric_name].relevant_columns:
                add_column(
                    (reference["t_name"], reference["c_name"]),
                    "metric_dependency",
                )

        for (t_name, c_name, _), _, _ in ranked_values:
            add_column((t_name, c_name), "value_owner")

        relations: dict[tuple[str, str, str, str], SemanticRelation] = {}
        if include_relations:
            participating_tables = {
                context.info.t_name for context in contexts.values()
            }
            for t_name in sorted(participating_tables):
                table_info = all_tables.get(t_name)
                if table_info is None:
                    continue
                for primary_key in table_info.primary_key_columns:
                    add_column((t_name, primary_key), "primary_key")

            foreign_keys = sorted(
                (
                    column_info
                    for column_info in columns.values()
                    if column_info.t_name in participating_tables
                    and column_info.reference_t_name
                    and column_info.reference_c_name
                ),
                key=lambda column_info: (column_info.t_name, column_info.name),
            )
            for foreign_key in foreign_keys:
                target_t_name = cast(str, foreign_key.reference_t_name)
                target_c_name = cast(str, foreign_key.reference_c_name)
                if target_t_name not in allowed_tables:
                    continue
                source_key = (foreign_key.t_name, foreign_key.name)
                target_key = (target_t_name, target_c_name)
                add_column(source_key, "foreign_key")
                add_column(target_key, "reference_target")
                if source_key in contexts and target_key in contexts:
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

        if context_truncated:
            warnings.append(
                f"Column context truncated at {_MAX_CONTEXT_COLUMNS} resources"
            )

        column_results: list[SemanticColumnResult] = []
        for context in contexts.values():
            column_info = context.info
            index_status = self._index_status(column_info)
            if index_status != "current" and self._has_semantic_index_match(
                context.match_reasons
            ):
                warnings.append(
                    "Column semantic index is "
                    f"{index_status}: {column_info.t_name}.{column_info.name}"
                )
            column_results.append(
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

        context_table_names = {context.info.t_name for context in contexts.values()}
        table_contexts = [
            SemanticTableContext(
                name=table_info.name,
                role=table_info.role,
                description=table_info.description,
                primary_key_columns=table_info.primary_key_columns,
                meta_version=table_info.meta_version,
            )
            for t_name in sorted(context_table_names)
            if (table_info := all_tables.get(t_name)) is not None
        ]

        return (
            column_results,
            table_contexts,
            list(relations.values()),
            context_truncated,
        )

    @staticmethod
    def _rrf_score(rank: int) -> float:
        """计算倒数排名融合分数"""
        return 1 / (_RRF_K + rank)

    @staticmethod
    def _index_status(item: ColumnInfo | MetricInfo) -> SemanticIndexStatus:
        """根据元数据和索引版本判断索引状态"""
        if item.index_version <= 0:
            return "missing"
        if item.index_version < item.meta_version:
            return "stale"
        return "current"

    @staticmethod
    def _value_sync_status(status: str | None) -> ValueSyncStatus | None:
        """将数据库字段值同步状态收窄到响应枚举"""
        if status in {"syncing", "succeeded", "failed"}:
            return cast(ValueSyncStatus, status)
        return None

    @staticmethod
    def _has_semantic_index_match(match_reasons: list[str]) -> bool:
        """判断候选是否来自全文或向量语义索引"""
        return any(
            reason.startswith(("fulltext:", "vector:")) for reason in match_reasons
        )

    @staticmethod
    def _raise_if_cancelled(error: BaseException) -> None:
        """避免将任务取消降级为普通检索失败"""
        if isinstance(error, asyncio.CancelledError):
            raise error

    @staticmethod
    def _format_exception(error: BaseException) -> str:
        """生成精简后端错误描述"""
        return f"{type(error).__name__}: {error}"

    @classmethod
    def _append_backend_warning(
        cls,
        warnings: list[str],
        backend_name: str,
        error: BaseException,
    ) -> None:
        """为同一后端稳定去重降级警告"""
        warning = (
            f"{backend_name} retrieval unavailable: {cls._format_exception(error)}"
        )
        if warning not in warnings:
            warnings.append(warning)
