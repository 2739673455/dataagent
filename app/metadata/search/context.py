"""依据候选排名补全字段、外键、主键和表上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.metadata.contracts import (
    ColumnKey,
    SemanticColumnRecallResult,
    SemanticIndexStatus,
    SemanticMatchReason,
    SemanticTableContext,
    column_reference_key,
)
from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo

ValueKey = tuple[str, str, str]
_MAX_RANKED_CONTEXT_COLUMNS = 30
_COLUMN_EXAMPLE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class SemanticCatalog:
    """语义召回使用的完整元数据目录。"""

    tables: dict[str, TableInfo]
    columns: dict[ColumnKey, ColumnInfo]
    metrics: dict[str, MetricInfo]


@dataclass(frozen=True, slots=True)
class RankedCandidates:
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


class ColumnContextBuilder:
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
        ranked: RankedCandidates,
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

    def _add_ranked_resources(self, ranked: RankedCandidates) -> None:
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
        """以参与表快照为起点，补充一层外键关联及其目标字段。"""
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
            index_status = semantic_index_status(column_info)
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


def semantic_index_status(item: ColumnInfo | MetricInfo) -> SemanticIndexStatus:
    """根据元数据和索引版本判断索引状态。"""
    if item.index_version <= 0:
        return "missing"
    if item.index_version < item.meta_version:
        return "stale"
    return "current"
