"""Explorer 语义召回工具定义。"""

from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from loguru import logger

from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticResourceType,
)
from app.metadata.services.recall_handler import SemanticRecallHandler


def _semantic_recall_payload(
    response: SemanticResourceRecallResponse,
) -> dict[str, Any]:
    """投影模型执行 SQL 所需的元数据。"""
    values_by_column: dict[tuple[str, str], list[str]] = {}
    for item in response.values:
        values_by_column.setdefault((item.t_name, item.c_name), []).append(item.value)

    tables: dict[str, dict[str, Any]] = {
        item.name: {
            **item.model_dump(include={"role", "description", "primary_key_columns"}),
            "columns": {},
        }
        for item in response.tables
    }
    for item in response.columns:
        table = tables.get(item.t_name)
        if table is None:
            continue
        column = item.model_dump(
            include={
                "type",
                "description",
                "alias",
                "examples",
                "reference_t_name",
                "reference_c_name",
            }
        )
        values = values_by_column.get((item.t_name, item.name))
        if values:
            column["values"] = values
        table["columns"][item.name] = column

    return {
        "tables": tables,
        "metrics": {
            item.name: item.model_dump(
                include={"description", "alias", "relevant_columns"}
            )
            for item in response.metrics
        },
    }


def create_semantic_recall_tool(recall: SemanticRecallHandler) -> BaseTool:
    """创建只负责协议转换的 Explorer 语义召回工具。"""

    @tool(args_schema=SemanticResourceRecallRequest)
    async def recall_context(
        runtime: ToolRuntime,
        resource_types: list[SemanticResourceType],
        terms: list[str],
        limit_per_type: int = 5,
    ) -> dict[str, Any]:
        """检索字段、指标和字段取值，直接返回本次元数据结果。"""
        request = SemanticResourceRecallRequest.model_construct(
            terms=terms, resource_types=resource_types, limit_per_type=limit_per_type
        )
        try:
            user_id = runtime.config.get("configurable", {})["user_id"]
            response = await recall.search(user_id, request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("语义资源召回失败")
            return {
                "status": "error",
                "message": "语义资源召回失败",
                "details": [
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            }
        return _semantic_recall_payload(response)

    return recall_context
