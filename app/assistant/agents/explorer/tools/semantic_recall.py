"""Explorer 语义召回工具定义。"""

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.assistant.agents.explorer import semantic_recall_handler
from app.metadata.models.recall import SemanticRecallResourceDeletion


def create_semantic_recall_tools() -> list[BaseTool]:
    """创建只负责协议转换的 Explorer 语义召回工具。"""

    @tool
    async def recall_context(
        runtime: ToolRuntime,
        query: Annotated[
            str,
            (
                "当前会话内召回上下文的稳定业务键。后续补充检索必须原样复用，"
                "只调整 terms 和 resource_types"
            ),
        ],
        resource_types: Annotated[
            list[Literal["column", "metric", "value"]],
            "需要检索的字段、指标或字段值资源类型，可多选",
        ],
        terms: Annotated[
            list[str],
            "用于检索的业务词或同义词，至少 1 个且最多 20 个",
        ],
        limit_per_type: Annotated[int, "每类候选的最大数量，范围 1 到 20"] = 5,
    ) -> dict[str, Any]:
        """按稳定 query 累计召回语义资源和历史 SQL 经验。"""
        return await semantic_recall_handler.recall_context(
            runtime.config,
            query,
            resource_types,
            terms,
            limit_per_type,
        )

    @tool
    async def list_recalls(
        runtime: ToolRuntime,
        limit: Annotated[int, "返回最近记录的数量，范围 1 到 100"] = 20,
    ) -> dict[str, Any]:
        """列出当前会话中每个 query 的最新累计召回记录。"""
        return await semantic_recall_handler.list_recalls(runtime.config, limit)

    @tool
    async def get_recall(
        runtime: ToolRuntime,
        query: Annotated[
            str,
            "需要读取的稳定 query，必须与 recall_context 使用的 query 完全一致",
        ],
    ) -> dict[str, Any]:
        """按 query 读取当前会话的最新累计召回记录。"""
        return await semantic_recall_handler.get_recall(runtime.config, query)

    @tool
    async def merge_recalls(
        runtime: ToolRuntime,
        target_query: Annotated[str, "接收累计结果并保留的目标 query"],
        source_query: Annotated[str, "提供结果并在合并后删除的来源 query"],
    ) -> dict[str, Any]:
        """合并来源 query 的语义资源并删除来源。"""
        return await semantic_recall_handler.merge_recalls(
            runtime.config,
            target_query,
            source_query,
        )

    @tool
    async def delete_recalls(
        runtime: ToolRuntime,
        deletions: Annotated[
            list[SemanticRecallResourceDeletion],
            (
                "待删除的 query 上下文树。未提供资源选择器时删除整个 query；"
                "同一 query 在一次调用中只能出现一次"
            ),
        ],
    ) -> dict[str, Any]:
        """删除当前会话 query 的全部上下文或其中指定资源。"""
        return await semantic_recall_handler.delete_recalls(runtime.config, deletions)

    return [
        recall_context,
        list_recalls,
        get_recall,
        merge_recalls,
        delete_recalls,
    ]
