"""Explorer 语义召回工具定义。"""

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.assistant.agents.explorer import semantic_recall_handler
from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime


def create_semantic_recall_tools(recall: SemanticRecallRuntime) -> list[BaseTool]:
    """创建只负责协议转换的 Explorer 语义召回工具。"""

    @tool
    async def recall_context(
        runtime: ToolRuntime,
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
        """检索字段、指标和字段取值，直接返回本次元数据结果。"""
        return await semantic_recall_handler.recall_context(
            runtime.config,
            resource_types,
            terms,
            limit_per_type,
            recall=recall,
        )

    return [recall_context]
