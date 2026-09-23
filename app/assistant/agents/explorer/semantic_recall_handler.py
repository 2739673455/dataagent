"""Explorer 语义资源召回用例。"""

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import ValidationError

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.agents.explorer.semantic_recall_payload import (
    semantic_recall_payload,
)
from app.metadata.models.search import SemanticResourceRecallRequest


def _tool_error_response(
    message: str,
    error: Exception,
) -> dict[str, Any]:
    """构造包含异常类别和原因的工具错误响应。"""
    detail = str(error).strip() or "异常未提供详情"
    return {
        "status": "error",
        "message": message,
        "details": [{"type": type(error).__name__, "msg": detail}],
    }


async def recall_context(
    config: RunnableConfig,
    resource_types: list[Literal["column", "metric", "value"]],
    terms: list[str],
    limit_per_type: int,
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """直接返回本次召回结果，供后续轮次持续使用。"""
    try:
        request = SemanticResourceRecallRequest(
            terms=terms,
            resource_types=resource_types,
            limit_per_type=limit_per_type,
        )
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "语义召回请求无效",
            "details": exc.errors(include_url=False),
        }
    try:
        user_id = config.get("configurable", {}).get("user_id")
        if not isinstance(user_id, int):
            raise TypeError("配置中未找到用户身份")
        response = await recall.search(user_id, request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("语义资源召回失败")
        return _tool_error_response("语义资源召回失败", exc)
    return semantic_recall_payload(response)
