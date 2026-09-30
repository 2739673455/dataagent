"""Explorer 召回工具：参数校验、用例调用和结果投影。"""

from typing import Any

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.agents.explorer.semantic_recall_protocol import (
    resolve_semantic_recall_identity,
    semantic_recall_deletion_result,
    semantic_recall_reference,
)
from app.assistant.agents.tools.errors import tool_error
from app.metadata.errors import SemanticQueriesNotFoundError, SemanticRecallSaveError
from app.metadata.models.recall import (
    DeleteRecallsRequest,
    GetRecallRequest,
    ListRecallsRequest,
    MergeRecallsRequest,
    RecallContextRequest,
    SemanticRecallResourceDeletion,
)
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceType,
)
from app.metadata.services.recall_application import SemanticRecallService


def _recall_error(
    message: str, error: Exception, *, missing_message: str = ""
) -> dict[str, Any]:
    """保留记录缺失与其他业务失败的不同响应。"""
    if missing_message and isinstance(error, SemanticQueriesNotFoundError):
        return {"status": "error", "message": missing_message, "queries": error.queries}
    logger.opt(exception=error).error(message)
    if isinstance(error, SemanticRecallSaveError):
        cause = error.__cause__
        return tool_error(
            "无法保存语义召回快照", cause if isinstance(cause, Exception) else error
        )
    return tool_error(message, error)


def create_semantic_recall_tools(recall: SemanticRecallService) -> list[BaseTool]:
    """请求模型由工具框架校验，业务编排由 metadata 承担。"""

    @tool(args_schema=RecallContextRequest)
    async def recall_context(
        config: RunnableConfig,
        query: str,
        resource_types: list[SemanticResourceType],
        terms: list[str],
        limit_per_type: int = 5,
    ) -> dict[str, Any]:
        """按稳定 query 累计召回语义资源和历史 SQL 经验，terms 为 1 至 20 个。"""
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(config)
            record = await recall.recall_context(
                user_id,
                conversation_id,
                query,
                SemanticResourceRecallRequest.model_construct(
                    terms=terms,
                    resource_types=resource_types,
                    limit_per_type=limit_per_type,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return _recall_error("语义资源召回失败", exc)
        return semantic_recall_reference(record)

    @tool(args_schema=ListRecallsRequest)
    async def list_recalls(config: RunnableConfig, limit: int = 20) -> dict[str, Any]:
        """列出当前会话中每个 query 的最新累计召回记录。"""
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(config)
            records = await recall.list_recalls(user_id, conversation_id, limit)
        except Exception as exc:  # noqa: BLE001
            return _recall_error("获取语义召回列表失败", exc)
        return {
            "status": "success",
            "recalls": [
                {
                    "query": record.query,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in records
            ],
        }

    @tool(args_schema=GetRecallRequest)
    async def get_recall(config: RunnableConfig, query: str) -> dict[str, Any]:
        """按 query 读取当前会话的最新累计召回记录。"""
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(config)
            record = await recall.get_recall(user_id, conversation_id, query)
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "加载语义召回记录失败", exc, missing_message="未找到指定的语义召回记录"
            )
        return semantic_recall_reference(record)

    @tool(args_schema=MergeRecallsRequest)
    async def merge_recalls(
        config: RunnableConfig, target_query: str, source_query: str
    ) -> dict[str, Any]:
        """合并来源 query 的语义资源并删除来源。"""
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(config)
            record = await recall.merge_recalls(
                user_id, conversation_id, target_query, source_query
            )
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "无法合并语义召回记录",
                exc,
                missing_message="未找到待合并的语义召回记录",
            )
        return semantic_recall_reference(record)

    @tool(args_schema=DeleteRecallsRequest)
    async def delete_recalls(
        config: RunnableConfig, deletions: list[SemanticRecallResourceDeletion]
    ) -> dict[str, Any]:
        """删除当前会话 query 的全部上下文或其中指定资源。"""
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(config)
            await recall.delete_recalls(user_id, conversation_id, deletions)
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "无法删除语义召回记录",
                exc,
                missing_message="未找到待删除的语义召回记录",
            )
        return semantic_recall_deletion_result(deletions)

    return [recall_context, list_recalls, get_recall, merge_recalls, delete_recalls]
