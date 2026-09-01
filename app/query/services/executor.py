"""受控只读查询执行与会话产物写入。"""

import base64
import csv
import hashlib
import json
import math
import re
import tempfile
import unicodedata
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from itertools import islice
from typing import Any, BinaryIO, Protocol
from uuid import UUID, uuid4

from loguru import logger

from app.query.models.execution import (
    AnalysisQueryResult,
    QueryBatch,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryResultColumn,
    QueryTimeRange,
)
from app.query.models.validation import QueryValidationResult
from app.sandbox.paths import SandboxSessionScope
from app.shared.contracts.analysis import AgentSessionKey

_SAMPLE_STRING_MAX_CHARS = 512
_SAMPLE_COLLECTION_MAX_ITEMS = 20
_SAMPLE_MAX_DEPTH = 4
_QUERY_ARTIFACT_STEM_MAX_BYTES = 120
_QUERY_ARTIFACT_UNIQUE_SUFFIX_LENGTH = 4
_PLAN_NODE_PATTERN = re.compile(r"(?:^|\s|\|)\d+\s*:\s*([A-Za-z][A-Za-z0-9_]*)")
_PLAN_UNNUMBERED_NODE_PATTERN = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*(?:Node|_NODE))\b",
    re.IGNORECASE,
)
_PLAN_SCAN_PATTERN = re.compile(
    r"\b(?:[A-Za-z]+ScanNode|[A-Za-z_]*SCAN_NODE)\b", re.IGNORECASE
)
_PLAN_NUMBER_CAPTURE = (
    r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?!,\d|[\d.eE])"
)
_PLAN_CARDINALITY_PATTERN = re.compile(
    r"\bcardinality\s*[=:]\s*" + _PLAN_NUMBER_CAPTURE,
    re.IGNORECASE,
)
_PLAN_AVG_ROW_SIZE_PATTERN = re.compile(
    r"\bavgRowSize\s*[=:]\s*" + _PLAN_NUMBER_CAPTURE,
    re.IGNORECASE,
)


class ReadonlyQueryRepository(Protocol):
    """只读查询执行存储的最小接口。"""

    async def explain(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> tuple[str, ...]:
        """返回受资源限制约束的查询执行计划。"""
        ...

    def stream(
        self,
        sql: str,
        limits: QueryExecutionLimits,
        options: QueryExecutionOptions,
    ) -> AsyncGenerator[QueryBatch]:
        """按批次流式读取受控查询结果。"""
        ...


class QueryArtifactStore(Protocol):
    """查询产物写入会话沙箱的最小接口。"""

    async def write_artifact(
        self,
        user_id: int,
        conversation_id: UUID,
        path: str,
        content: BinaryIO,
    ) -> None:
        """将查询产物写入指定用户的会话沙箱。"""
        ...


class QueryRejectedError(ValueError):
    """SQL 未通过确定性安全校验。"""

    def __init__(self, result: QueryValidationResult) -> None:
        """保存完整校验结果并汇总拒绝原因。"""
        self.result = result
        message = "; ".join(issue.message for issue in result.issues)
        super().__init__(message or "SQL 查询已被拒绝")


class QueryResultShapeError(RuntimeError):
    """数据库返回的结果结构不稳定或不适合文件输出。"""


class QueryPlanUnavailableError(RuntimeError):
    """Doris 查询计划缺少可验证的扫描估算。"""


@dataclass(frozen=True, slots=True)
class QueryPlanEstimate:
    """从 Doris EXPLAIN 提取的物理扫描估算。"""

    scan_nodes: int
    scan_rows: int
    scan_bytes: int


@dataclass(frozen=True, slots=True)
class SuccessfulQueryExecution:
    """成功查询的规范化 SQL、资产血缘和结果摘要。"""

    session_key: AgentSessionKey
    raw_sql: str
    normalized_sql: str
    validation: QueryValidationResult
    plan_estimate: QueryPlanEstimate | None
    result: AnalysisQueryResult


@dataclass(slots=True)
class _ScanNodeEstimate:
    """一个 ScanNode 的未完成估算。"""

    cardinality: float | None = None
    avg_row_size: float | None = None


@dataclass(slots=True)
class _ColumnStats:
    """流式构造字段 Schema 和时间范围所需的状态。"""

    inferred_type: str | None = None
    nullable: bool = False
    time_start: str | None = None
    time_end: str | None = None

    def observe(self, value: Any) -> None:
        """合并一个字段值的类型和时间信息。"""
        if value is None:
            self.nullable = True
            return
        value_type = _value_type(value)
        self.inferred_type = _merge_types(self.inferred_type, value_type)
        temporal_value = _temporal_value(value)
        if temporal_value is None:
            return
        if self.time_start is None or temporal_value < self.time_start:
            self.time_start = temporal_value
        if self.time_end is None or temporal_value > self.time_end:
            self.time_end = temporal_value


@dataclass(slots=True)
class _Utf8Writer:
    """将 CSV 文本编码为 UTF-8 后写入二进制文件。"""

    destination: BinaryIO

    def write(self, value: str) -> int:
        """编码并写入一段 CSV 文本。"""
        encoded = value.encode("utf-8")
        written = self.destination.write(encoded)
        if written != len(encoded):
            raise OSError("临时查询输出写入不完整")
        return len(value)


class AnalysisQueryService:
    """流式执行已通过 Guard 的查询并写入当前会话沙箱。"""

    def __init__(
        self,
        query_repo: ReadonlyQueryRepository,
        artifact_store: QueryArtifactStore,
        limits: QueryExecutionLimits,
        options: QueryExecutionOptions,
    ) -> None:
        """初始化只读查询服务。"""
        self._query_repo = query_repo
        self._artifact_store = artifact_store
        self._limits = limits
        self._options = options

    async def execute(
        self,
        session_key: AgentSessionKey,
        sql: str,
        validation: QueryValidationResult,
        *,
        purpose: str,
    ) -> SuccessfulQueryExecution:
        """执行已校验查询，返回完整的成功执行信息。"""
        sql_fingerprint = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
        logger.info(
            "开始执行只读查询: "
            f"conversation_id={session_key.conversation_id}, "
            f"analysis_id={session_key.analysis_id}, "
            f"sql_fingerprint={sql_fingerprint}"
        )
        normalized_sql = validation.normalized_sql
        if not validation.valid or normalized_sql is None:
            raise QueryRejectedError(validation)

        estimate: QueryPlanEstimate | None = None
        if validation.query_kind == "business":
            plan = await self._query_repo.explain(normalized_sql, self._limits)
            estimate = _estimate_doris_query_plan(
                plan,
                require_scan=bool(validation.tables),
            )
        relative_path = (
            f"sessions/{session_key.analysis_id}/{session_key.agent_type}/"
            f"{session_key.session_id}/{_query_artifact_filename(purpose)}"
        )
        with tempfile.TemporaryFile(mode="w+b") as temporary_file:
            summary = await self._execute_to_csv(
                temporary_file,
                normalized_sql,
            )
            temporary_file.seek(0)
            await self._artifact_store.write_artifact(
                session_key.user_id,
                session_key.conversation_id,
                relative_path,
                temporary_file,
            )
        workspace = SandboxSessionScope(
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        ).workspace_path(session_key.conversation_id)
        result = AnalysisQueryResult(
            path=f"{workspace}/{relative_path.rsplit('/', 1)[-1]}",
            columns=summary.columns,
            row_count=summary.row_count,
            time_range=summary.time_range,
            sample=summary.sample,
        )
        details = SuccessfulQueryExecution(
            session_key=session_key,
            raw_sql=sql,
            normalized_sql=normalized_sql,
            validation=validation,
            plan_estimate=estimate,
            result=result,
        )
        logger.info(
            "只读查询执行完成: "
            f"conversation_id={session_key.conversation_id}, "
            f"analysis_id={session_key.analysis_id}, "
            f"sql_fingerprint={sql_fingerprint}, "
            f"row_count={details.result.row_count}, "
            f"column_count={len(details.result.columns)}, "
            f"scan_rows={details.plan_estimate.scan_rows if details.plan_estimate else None}, "
            f"scan_bytes={details.plan_estimate.scan_bytes if details.plan_estimate else None}, "
            f"artifact_path={details.result.path}"
        )
        return details

    async def _execute_to_csv(
        self,
        temporary_file: BinaryIO,
        sql: str,
    ) -> "_QuerySummary":
        """流式执行查询并写入 CSV，同时保留字段统计与少量样例。"""
        writer = csv.writer(_Utf8Writer(temporary_file), lineterminator="\n")
        column_names: tuple[str, ...] | None = None
        column_stats: list[_ColumnStats] = []
        sample: list[dict[str, Any]] = []
        row_count = 0
        async with aclosing(
            self._query_repo.stream(sql, self._limits, self._options)
        ) as batches:
            async for batch in batches:
                if column_names is None:
                    column_names = batch.column_names
                    self._validate_column_names(column_names)
                    column_stats = [_ColumnStats() for _ in column_names]
                    writer.writerow(_csv_value(name) for name in column_names)
                elif batch.column_names != column_names:
                    raise QueryResultShapeError("流式查询各批次返回的列结构不一致")
                for row in batch.rows:
                    if len(row) != len(column_names):
                        raise QueryResultShapeError(
                            "查询结果行的列数与元数据声明不一致"
                        )
                    for stats, value in zip(column_stats, row, strict=True):
                        stats.observe(value)
                    writer.writerow(_csv_value(value) for value in row)
                    if len(sample) < self._options.sample_rows:
                        sample.append(
                            {
                                name: _summary_value(value)
                                for name, value in zip(column_names, row, strict=True)
                            }
                        )
                    row_count += 1
        if column_names is None:
            raise QueryResultShapeError("数据库未返回有效的结果元数据")
        temporary_file.flush()
        return _QuerySummary(
            columns=[
                QueryResultColumn(
                    name=name,
                    type=stats.inferred_type or "unknown",
                    nullable=stats.nullable or row_count == 0,
                )
                for name, stats in zip(column_names, column_stats, strict=True)
            ],
            row_count=row_count,
            time_range={
                name: QueryTimeRange(start=stats.time_start, end=stats.time_end)
                for name, stats in zip(column_names, column_stats, strict=True)
                if stats.time_start is not None and stats.time_end is not None
            },
            sample=sample,
        )

    @staticmethod
    def _validate_column_names(column_names: tuple[str, ...]) -> None:
        """要求数据库返回非空且唯一的字段名。"""
        if not column_names or any(not name for name in column_names):
            raise QueryResultShapeError("查询结果列名不能为空")
        normalized = [name.casefold() for name in column_names]
        if len(normalized) != len(set(normalized)):
            raise QueryResultShapeError("查询结果列名不能重复")


@dataclass(frozen=True, slots=True)
class _QuerySummary:
    """临时文件写入结束后的内存摘要。"""

    columns: list[QueryResultColumn]
    row_count: int
    time_range: dict[str, QueryTimeRange]
    sample: list[dict[str, Any]]


def _query_artifact_filename(purpose: str) -> str:
    """将查询目的转换为安全、可辨识且不会覆盖旧产物的 CSV 文件名。"""
    normalized = unicodedata.normalize("NFKC", purpose).strip()
    stem_parts: list[str] = []
    separator_pending = False
    for character in normalized:
        if character.isalnum():
            if separator_pending and stem_parts:
                stem_parts.append("_")
            stem_parts.append(character)
            separator_pending = False
        else:
            separator_pending = True

    stem = "".join(stem_parts).rstrip("_") or "query_result"
    encoded_size = 0
    truncated: list[str] = []
    for character in stem:
        character_size = len(character.encode("utf-8"))
        if encoded_size + character_size > _QUERY_ARTIFACT_STEM_MAX_BYTES:
            break
        truncated.append(character)
        encoded_size += character_size
    safe_stem = "".join(truncated).rstrip("_") or "query_result"
    unique_suffix = uuid4().hex[:_QUERY_ARTIFACT_UNIQUE_SUFFIX_LENGTH]
    return f"{safe_stem}_{unique_suffix}.csv"


def _estimate_doris_query_plan(
    plan: tuple[str, ...],
    *,
    require_scan: bool,
) -> QueryPlanEstimate:
    """解析 Doris ScanNode 的 cardinality 与 avgRowSize。"""
    completed: list[_ScanNodeEstimate] = []
    current: _ScanNodeEstimate | None = None

    def finish_current() -> None:
        """提交当前扫描节点的估算结果。"""
        nonlocal current
        if current is not None:
            completed.append(current)
            current = None

    for entry in plan:
        for line in entry.splitlines() or [entry]:
            node_match = _PLAN_NODE_PATTERN.search(line)
            unnumbered_node_match = _PLAN_UNNUMBERED_NODE_PATTERN.search(line)
            node_name = (
                node_match.group(1)
                if node_match is not None
                else (
                    unnumbered_node_match.group(1)
                    if unnumbered_node_match is not None
                    else None
                )
            )
            if node_name is not None:
                finish_current()
                if "scan" in node_name.casefold():
                    current = _ScanNodeEstimate()
            elif _PLAN_SCAN_PATTERN.search(line):
                finish_current()
                current = _ScanNodeEstimate()

            if current is None:
                continue
            if cardinality_match := _PLAN_CARDINALITY_PATTERN.search(line):
                current.cardinality = float(cardinality_match.group(1).replace(",", ""))
            if avg_row_size_match := _PLAN_AVG_ROW_SIZE_PATTERN.search(line):
                current.avg_row_size = float(
                    avg_row_size_match.group(1).replace(",", "")
                )
    finish_current()

    if require_scan and not completed:
        raise QueryPlanUnavailableError("Doris EXPLAIN 未包含物理扫描节点估算信息")
    if any(
        node.cardinality is None
        or node.cardinality < 0
        or node.avg_row_size is None
        or node.avg_row_size < 0
        or (node.cardinality > 0 and node.avg_row_size == 0)
        for node in completed
    ):
        raise QueryPlanUnavailableError("Doris EXPLAIN 物理扫描节点估算信息不完整")
    scan_rows = sum(math.ceil(node.cardinality or 0) for node in completed)
    scan_bytes = sum(
        math.ceil((node.cardinality or 0) * (node.avg_row_size or 0))
        for node in completed
    )
    return QueryPlanEstimate(
        scan_nodes=len(completed),
        scan_rows=scan_rows,
        scan_bytes=scan_bytes,
    )


def _value_type(value: Any) -> str:
    """推断结果值的稳定 Schema 类型。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, float):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, time):
        return "time"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "binary"
    if isinstance(value, (dict, list, tuple)):
        return "json"
    return type(value).__name__


def _merge_types(current: str | None, observed: str) -> str:
    """合并同一字段跨行观察到的运行时类型。"""
    if current is None or current == observed:
        return observed
    if {current, observed} <= {"integer", "decimal", "number"}:
        return "number"
    if {current, observed} <= {"date", "datetime"}:
        return "datetime"
    return "mixed"


def _temporal_value(value: Any) -> str | None:
    """把日期时间值转换为可稳定比较的 ISO 文本。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def _summary_value(value: Any, depth: int = 0) -> Any:
    """转换为可以放入工具返回值的 JSON 兼容数据。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if len(value) <= _SAMPLE_STRING_MAX_CHARS:
            return value
        return f"{value[:_SAMPLE_STRING_MAX_CHARS]}…"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        byte_limit = _SAMPLE_STRING_MAX_CHARS * 3 // 4
        encoded = base64.b64encode(value[:byte_limit]).decode("ascii")
        return f"{encoded}…" if len(value) > byte_limit else encoded
    if depth >= _SAMPLE_MAX_DEPTH:
        return "<nested value omitted>"
    if isinstance(value, dict):
        items = list(islice(value.items(), _SAMPLE_COLLECTION_MAX_ITEMS))
        summary = {str(key): _summary_value(item, depth + 1) for key, item in items}
        if len(value) > _SAMPLE_COLLECTION_MAX_ITEMS:
            summary["__truncated__"] = len(value) - _SAMPLE_COLLECTION_MAX_ITEMS
        return summary
    if isinstance(value, (list, tuple)):
        items = [
            _summary_value(item, depth + 1)
            for item in value[:_SAMPLE_COLLECTION_MAX_ITEMS]
        ]
        if len(value) > _SAMPLE_COLLECTION_MAX_ITEMS:
            items.append(f"<{len(value) - _SAMPLE_COLLECTION_MAX_ITEMS} items omitted>")
        return items
    rendered = str(value)
    if len(rendered) <= _SAMPLE_STRING_MAX_CHARS:
        return rendered
    return f"{rendered[:_SAMPLE_STRING_MAX_CHARS]}…"


def _csv_value(value: Any) -> Any:
    """转换为不依赖 Python repr 的 CSV 单元格值。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return _escape_csv_formula(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _json_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return value


def _escape_csv_formula(value: str) -> str:
    """阻止电子表格把不可信字符串解释为公式。"""
    for character in value:
        if character.isspace() or unicodedata.category(character).startswith("C"):
            continue
        return f"'{value}" if character in "=+-@" else value
    return value


def _json_value(value: Any) -> Any:
    """完整保留 CSV 中嵌套值并转换为 JSON 兼容数据。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)
