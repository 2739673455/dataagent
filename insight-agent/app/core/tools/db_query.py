import csv
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from app.utils.http_client import get_http_client
from langchain.tools import tool

DB_QUERY_URL = "http://127.0.0.1:8000/api/query"
PREVIEW_ROWS = 5


async def _stream_db_query(query: str) -> AsyncIterator[dict[str, Any]]:
    """流式调用 data-agent 查询接口并产出 SSE 消息。"""
    client = get_http_client()
    async with client.stream(
        "POST",
        DB_QUERY_URL,
        json={"query": query},
        headers={"accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()

        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue

            payload = line.removeprefix("data:").strip()
            if not payload:
                continue

            try:
                yield json.loads(payload)
            except json.JSONDecodeError as exc:
                yield {
                    "type": "error",
                    "message": f"SSE payload JSON decode failed: {exc}",
                    "raw_payload": payload,
                }


def _normalize_rows(result: Any) -> list[dict[str, Any]] | None:
    """将查询结果规范化为表格行列表，非表格结果返回 None。"""
    if not isinstance(result, list):
        return None
    if not result:
        return []
    if not all(isinstance(row, dict) for row in result):
        return None
    return result


def _write_csv_result(file_path: Path, rows: list[dict[str, Any]]) -> list[str]:
    """将表格结果写入 CSV 文件并返回字段列表。"""
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with file_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return fieldnames


def _write_json_result(file_path: Path, result: Any) -> None:
    """将非表格结果写入 JSON 文件。"""
    with file_path.open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, default=str)


def create_db_query_tool(workspace_dir: str | Path):
    """创建绑定到指定会话工作区的数据库查询工具。"""
    workspace_dir = Path(workspace_dir)
    output_dir = workspace_dir / "db_query_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    @tool
    async def db_query(
        query: Annotated[
            str,
            "用户的自然语言数据查询需求，例如查看销量、库存、退货率等业务问题。",
        ],
    ) -> dict[str, Any]:
        """查询数据库业务数据，将最终结果写入当前会话工作区，并返回文件路径、字段和前几行数据。"""
        result: Any = None

        try:
            async for chunk in _stream_db_query(query):
                chunk_type = chunk.get("type")

                if chunk_type == "result":
                    result = chunk.get("data")
                    continue

                if chunk_type == "error":
                    return {
                        "status": "error",
                        "message": chunk.get("message", "unknown error"),
                    }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "message": f"data query API returned HTTP {exc.response.status_code}",
            }
        except httpx.HTTPError as exc:
            return {
                "status": "error",
                "message": f"data query API request failed: {exc}",
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"unexpected db_query error: {exc}",
            }

        if result is None:
            return {
                "status": "error",
                "message": "data query API finished without result",
            }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tabular_rows = _normalize_rows(result)

        try:
            if tabular_rows is not None:
                file_path = output_dir / f"db_query_{timestamp}.csv"
                fields = _write_csv_result(file_path, tabular_rows)
                preview_rows = tabular_rows[:PREVIEW_ROWS]
                pandas_read_hint = f"pd.read_csv('{file_path.as_posix()}')"
            else:
                file_path = output_dir / f"db_query_{timestamp}.json"
                _write_json_result(file_path, result)
                fields = []
                preview_rows = (
                    result[:PREVIEW_ROWS] if isinstance(result, list) else [result]
                )
                pandas_read_hint = f"pd.read_json('{file_path.as_posix()}')"
        except Exception as exc:
            return {
                "status": "error",
                "message": f"failed to write query result file: {exc}",
            }

        return {
            "status": "success",
            "file_path": file_path.as_posix(),
            "file_format": file_path.suffix.lstrip("."),
            "pandas_read_hint": pandas_read_hint,
            "fields": fields,
            "preview_rows": preview_rows,
            "row_count": len(tabular_rows) if tabular_rows is not None else None,
        }

    return db_query
