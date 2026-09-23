"""受控只读查询执行与会话产物写入。"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import tempfile
import unicodedata
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from io import TextIOWrapper
from itertools import islice
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger

from app.query.errors import QueryRejectedError, QueryResultShapeError
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryResultColumn,
    QueryTimeRange,
)
from app.query.models.validation import QueryValidationResult
from app.sandbox.paths import SandboxSessionScope
from app.shared.contracts.analysis import AgentSessionKey

if TYPE_CHECKING:
    from app.query.repositories.doris import DorisQueryRepository
    from app.sandbox.manager import DockerSandboxManager

_SAMPLE_STRING_MAX_CHARS = 512
_SAMPLE_COLLECTION_MAX_ITEMS = 20
_SAMPLE_MAX_DEPTH = 4


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
        if not isinstance(value, date):
            return
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(UTC)
        temporal_value = value.isoformat()
        if self.time_start is None or temporal_value < self.time_start:
            self.time_start = temporal_value
        if self.time_end is None or temporal_value > self.time_end:
            self.time_end = temporal_value


class AnalysisQueryService:
    """流式执行已通过 Guard 的查询并写入当前会话沙箱。"""

    def __init__(
        self,
        query_repo: DorisQueryRepository,
        artifact_store: DockerSandboxManager,
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
        validation: QueryValidationResult,
        *,
        purpose: str,
    ) -> AnalysisQueryResult:
        """执行已校验查询，返回会话产物及结果摘要。"""
        normalized_sql = validation.normalized_sql
        if not validation.valid or normalized_sql is None:
            raise QueryRejectedError(validation)
        sql_fingerprint = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()[
            :16
        ]
        logger.info(
            "开始执行只读查询: "
            f"conversation_id={session_key.conversation_id}, "
            f"analysis_id={session_key.analysis_id}, "
            f"sql_fingerprint={sql_fingerprint}"
        )
        scope = SandboxSessionScope(
            session_key.analysis_id,
            session_key.agent_type,
            session_key.session_id,
        )
        normalized = unicodedata.normalize("NFKC", purpose).strip()
        stem = re.sub(r"[\W_]+", "_", normalized).strip("_") or "query_result"
        relative_path = f"{scope.relative_workspace}/{stem}.csv"
        with (
            tempfile.TemporaryFile(mode="w+b") as temporary_file,
            TextIOWrapper(temporary_file, encoding="utf-8", newline="") as csv_file,
        ):
            summary = await self._execute_to_csv(
                csv_file,
                normalized_sql,
            )
            temporary_file.seek(0)
            await self._artifact_store.write_artifact(
                session_key.user_id,
                session_key.conversation_id,
                relative_path,
                temporary_file,
            )
        workspace = scope.workspace_path(session_key.conversation_id)
        result = AnalysisQueryResult(
            path=f"{workspace}/{stem}.csv",
            columns=summary.columns,
            row_count=summary.row_count,
            time_range=summary.time_range,
            sample=summary.sample,
        )
        logger.info(
            "只读查询执行完成: "
            f"conversation_id={session_key.conversation_id}, "
            f"analysis_id={session_key.analysis_id}, "
            f"sql_fingerprint={sql_fingerprint}, "
            f"row_count={result.row_count}, "
            f"column_count={len(result.columns)}, "
            f"artifact_path={result.path}"
        )
        return result

    async def _execute_to_csv(
        self,
        csv_file: TextIO,
        sql: str,
    ) -> _QuerySummary:
        """流式执行查询并写入 CSV，同时保留字段统计与少量样例。"""
        writer = csv.writer(csv_file, lineterminator="\n")
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
                    if not column_names or any(not name for name in column_names):
                        raise QueryResultShapeError("查询结果列名不能为空")
                    if len({name.casefold() for name in column_names}) != len(
                        column_names
                    ):
                        raise QueryResultShapeError("查询结果列名不能重复")
                    column_stats = [_ColumnStats() for _ in column_names]
                    writer.writerow(_csv_value(name) for name in column_names)
                elif batch.column_names != column_names:
                    # CSV 和返回 Schema 共用首批列定义，中途变形会使产物无法可靠解析。
                    raise QueryResultShapeError("流式查询各批次返回的列结构不一致")
                for row in batch.rows:
                    if len(row) != len(column_names):
                        raise QueryResultShapeError(
                            "查询结果行的列数与元数据声明不一致"
                        )
                    for stats, value in zip(column_stats, row, strict=True):
                        stats.observe(value)
                    writer.writerow(_csv_value(value) for value in row)
                    # 完整结果持续写入文件，内存只保留固定数量的可展示样例。
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
        csv_file.flush()
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


@dataclass(frozen=True, slots=True)
class _QuerySummary:
    """临时文件写入结束后的内存摘要。"""

    columns: list[QueryResultColumn]
    row_count: int
    time_range: dict[str, QueryTimeRange]
    sample: list[dict[str, Any]]


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


def _summary_value(value: Any, depth: int = 0) -> Any:
    """转换为可以放入工具返回值的 JSON 兼容数据。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if len(value) <= _SAMPLE_STRING_MAX_CHARS:
            return value
        return f"{value[:_SAMPLE_STRING_MAX_CHARS]}…"
    if isinstance(value, bytes):
        byte_limit = _SAMPLE_STRING_MAX_CHARS * 3 // 4
        encoded = _scalar_value(value[:byte_limit])
        return f"{encoded}…" if len(value) > byte_limit else encoded
    if isinstance(value, (date, time, Decimal)):
        return _scalar_value(value)
    if depth >= _SAMPLE_MAX_DEPTH:
        return "<nested value omitted>"
    if isinstance(value, dict):
        items = islice(value.items(), _SAMPLE_COLLECTION_MAX_ITEMS)
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
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _json_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return _scalar_value(value)


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
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(_scalar_value(value))


def _scalar_value(value: Any) -> Any:
    """将日期时间、金额和二进制值转为文本，其他值原样返回。"""
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value
