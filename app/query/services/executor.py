"""受控只读查询执行与会话产物写入。"""

import base64
import csv
import json
import re
import tempfile
import unicodedata
from contextlib import aclosing
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from io import TextIOWrapper
from typing import Any, TextIO

from app.query.errors import QueryResultShapeError
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecutionOptions,
)
from app.query.repositories.doris import DorisQueryRepository
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.paths import SandboxSessionScope
from app.shared.contracts.analysis import AgentSessionKey

_SAMPLE_STRING_MAX_CHARS = 512


class AnalysisQueryService:
    """流式执行已通过 Guard 的查询并写入当前会话沙箱。"""

    def __init__(
        self,
        query_repo: DorisQueryRepository,
        artifact_store: DockerSandboxManager,
        options: QueryExecutionOptions,
    ) -> None:
        """初始化只读查询服务。"""
        self._query_repo = query_repo
        self._artifact_store = artifact_store
        self._options = options

    async def execute(
        self,
        session_key: AgentSessionKey,
        sql: str,
        *,
        purpose: str,
    ) -> AnalysisQueryResult:
        """执行已校验查询，返回会话产物及结果摘要。"""
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
                sql,
            )
            csv_file.flush()
            temporary_file.seek(0)
            await self._artifact_store.write_artifact(
                session_key.user_id,
                session_key.conversation_id,
                relative_path,
                temporary_file,
            )
        workspace = scope.workspace_path(session_key.conversation_id)
        result = AnalysisQueryResult(
            path=f"{workspace}/{relative_path.rsplit('/', 1)[-1]}",
            columns=summary.columns,
            row_count=summary.row_count,
            sample=summary.sample,
        )
        return result

    async def _execute_to_csv(
        self,
        csv_file: TextIO,
        sql: str,
    ) -> "_QuerySummary":
        """流式执行查询并写入 CSV，同时保留列名、行数与少量样例。"""
        writer = csv.writer(csv_file, lineterminator="\n")
        column_names: tuple[str, ...] | None = None
        sample: list[dict[str, Any]] = []
        row_count = 0
        async with aclosing(self._query_repo.stream(sql, self._options)) as batches:
            async for batch in batches:
                if column_names is None:
                    column_names = batch.column_names
                    if len({name.casefold() for name in column_names}) != len(
                        column_names
                    ):
                        raise QueryResultShapeError("查询结果列名不能重复")
                    writer.writerow(_csv_value(name) for name in column_names)
                elif batch.column_names != column_names:
                    # CSV 和返回列名共用首批列定义，中途变形会使产物无法可靠解析。
                    raise QueryResultShapeError("流式查询各批次返回的列结构不一致")
                for row in batch.rows:
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
        return _QuerySummary(
            columns=list(column_names), row_count=row_count, sample=sample
        )


@dataclass(frozen=True, slots=True)
class _QuerySummary:
    """临时文件写入结束后的内存摘要。"""

    columns: list[str]
    row_count: int
    sample: list[dict[str, Any]]


def _summary_value(value: Any) -> Any:
    """样例保留基本类型，其他值转为限长文本。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    rendered = _text_value(value)
    if len(rendered) <= _SAMPLE_STRING_MAX_CHARS:
        return rendered
    return f"{rendered[:_SAMPLE_STRING_MAX_CHARS]}…"


def _csv_value(value: Any) -> Any:
    """生成完整 CSV 单元格，字符串保留公式转义。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return _escape_csv_formula(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return _text_value(value)


def _text_value(value: Any) -> str:
    """统一日期、金额、二进制和嵌套值的文本格式。"""
    converted = _json_value(value)
    if isinstance(converted, (dict, list)):
        return json.dumps(converted, ensure_ascii=False, separators=(",", ":"))
    return str(converted)


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
