"""Planner 消息创建时间中间件"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

from app.analytics.message_metadata import stamp_message_created_at


def _stamp_response(response: ModelResponse[Any]) -> ModelResponse[Any]:
    for message in response.result:
        stamp_message_created_at(message)
    return response


class MessageTimestampMiddleware(AgentMiddleware[Any, Any, Any]):
    """在模型响应进入 LangGraph 状态前写入创建时间"""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return _stamp_response(handler(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return _stamp_response(await handler(request))
