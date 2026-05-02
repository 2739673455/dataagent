"""数据库查询工具 — 通过 data-agent SSE 接口查询业务数据，结果写入工作区文件"""

import csv
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from app.core.http_client import get_http_client
from app.core.settings import cfg
from langchain.tools import tool
from langgraph.prebuilt.tool_node import ToolRuntime

# data-agent 查询接口完整 URL
DB_QUERY_URL = cfg.data_agent.base_url + cfg.data_agent.query
# 预览行数：写入文件后返回给 Agent 的前几行数据，便于 Agent 理解结果结构
PREVIEW_ROWS = 5


async def _stream_db_query(query: str) -> AsyncIterator[dict[str, Any]]:
    """流式调用 data-agent 查询接口并逐条产出 SSE 消息"""
    client = get_http_client()
    async with client.stream(
        "POST",
        DB_QUERY_URL,
        json={"query": query},
        headers={"accept": "text/event-stream"},
    ) as resp:
        resp.raise_for_status()

        # data-agent 返回 SSE (Server-Sent Events) 流，逐行解析
        async for line in resp.aiter_lines():
            # SSE 协议：有效数据行以 "data:" 开头，空行和注释行跳过
            if not line or not line.startswith("data:"):
                continue

            # 去掉 "data:" 前缀并去除首尾空白
            payload = line.removeprefix("data:").strip()
            if not payload:
                continue

            # 尝试解析 JSON；解析失败时产出错误消息而非中断流
            try:
                yield json.loads(payload)
            except json.JSONDecodeError as exc:
                yield {
                    "type": "error",
                    "message": f"SSE payload JSON decode failed: {exc}",
                    "raw_payload": payload,
                }


def _as_tabular_rows(result: Any) -> list[dict[str, Any]] | None:
    """尝试将查询结果解释为表格行列表，无法解释时返回 None

    表格结果的特征：非空 list，且每个元素都是 dict。
    空列表视为合法表格（没有行但结构是表格），返回 []。
    """
    if not isinstance(result, list):
        return None
    if not result:
        return []
    if not all(isinstance(row, dict) for row in result):
        return None
    return result


def _write_csv_result(file_path: Path, rows: list[dict[str, Any]]) -> list[str]:
    """将表格行列表写入 CSV 文件并返回字段名列表

    字段名从所有行的 key 中并集收集，按首次出现顺序排列，
    避免某些行为 None 或缺少 key 时漏掉字段。
    """
    # 按首次出现顺序收集所有字段名
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
    """将非表格结果写入 JSON 文件

    default=str 确保 datetime、Decimal 等不可序列化类型也能落盘，
    不会因单个字段导致整个写入失败。
    """
    with file_path.open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, default=str)


@tool
async def db_query(
    runtime: ToolRuntime,
    query: Annotated[
        str, "用户的自然语言数据查询需求，例如查看销量、库存、退货率等业务问题"
    ],
    file_name: Annotated[str, "输出查询结果文件的文件名"],
) -> dict[str, Any]:
    """查询数据库业务数据，将最终结果写入当前会话工作区，并返回文件路径、字段和前几行数据

    流程：
    1. 从 runtime 获取当前会话工作区目录
    2. 流式调用 data-agent，收集 SSE 消息
    3. 将最终结果解释为表格（CSV）或非表格（JSON）写入工作区
    4. 返回文件路径、格式、字段列表、前几行预览，供 Agent 后续使用
    """
    # 从 LangGraph 运行时配置中提取工作区目录
    workspace_dir = runtime.config.get("configurable", {}).get("workspace_dir")
    if workspace_dir is None:
        return {"status": "error", "message": "workspace_dir not found in config"}
    workspace_dir = Path(workspace_dir)

    # 流式消费 data-agent 响应，收集 final result
    result: Any = None

    try:
        async for chunk in _stream_db_query(query):
            chunk_type = chunk.get("type")

            # "result" 类型承载最终查询结果
            if chunk_type == "result":
                result = chunk.get("data")
                continue

            # "error" 类型表示 data-agent 查询失败
            if chunk_type == "error":
                return {
                    "status": "error",
                    "message": chunk.get("message", "unknown error"),
                }
    except Exception as exc:
        # HTTP 错误、连接中断等底层异常
        return {
            "status": "error",
            "message": f"db_query failed: {type(exc).__name__}: {exc!r}",
        }

    # data-agent 返回了消息但缺少最终结果
    if result is None:
        return {
            "status": "error",
            "message": "data query API finished without result",
        }

    # 将结果写入工作区文件（表格→CSV，非表格→JSON）
    try:
        tabular_rows = _as_tabular_rows(result)
        if tabular_rows is not None:
            # 表格结果：写入 CSV，返回前 N 行预览
            file_path = workspace_dir / f"{file_name}.csv"
            fields = _write_csv_result(file_path, tabular_rows)
            preview_rows = tabular_rows[:PREVIEW_ROWS]
            pandas_read_hint = f"pd.read_csv('{file_path.as_posix()}')"
        else:
            # 非表格结果：写入 JSON，整体或前 N 项预览
            file_path = workspace_dir / f"{file_name}.json"
            _write_json_result(file_path, result)
            fields = []
            preview_rows = (
                result[:PREVIEW_ROWS] if isinstance(result, list) else [result]
            )
            pandas_read_hint = f"pd.read_json('{file_path.as_posix()}')"
    except Exception as exc:
        return {
            "status": "error",
            "message": (
                f"failed to write query result file: {type(exc).__name__}: {exc!r}"
            ),
        }

    return {
        # 操作状态标识，Agent 可据此判断查询是否成功
        "status": "success",
        # 结果文件的绝对路径（POSIX 风格，Agent 可直接用于读取）
        "file_path": file_path.as_posix(),
        # 文件格式后缀，不含点号："csv" 或 "json"，便于 Agent 选择解析方式
        "file_format": file_path.suffix.lstrip("."),
        # pandas 读取代码片段，Agent 可直接在 Python 代码中执行
        "pandas_read_hint": pandas_read_hint,
        # 表格结果的列名列表；非表格结果为空列表
        "fields": fields,
        # 前 N 行数据预览，帮助 Agent 在不需要读完整文件的情况下理解数据结构和内容
        "preview_rows": preview_rows,
        # 表格结果的总行数；非表格结果为 None
        "row_count": len(tabular_rows) if tabular_rows is not None else None,
    }
