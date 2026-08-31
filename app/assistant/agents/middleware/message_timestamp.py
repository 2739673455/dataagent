"""模型响应消息创建时间 Middleware。"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

from app.assistant.agents.contracts import MESSAGE_CREATED_AT_KEY


def _stamp_response(response: ModelResponse[Any]) -> ModelResponse[Any]:
    """为模型响应中的消息补充统一创建时间。"""
    for message in response.result:
        message.additional_kwargs.setdefault(
            MESSAGE_CREATED_AT_KEY,
            datetime.now(UTC).isoformat(),
        )
    return response


class MessageTimestampMiddleware(AgentMiddleware[Any, Any, Any]):
    """在模型响应进入 LangGraph 状态前写入创建时间。"""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """为同步模型响应写入创建时间。"""
        return _stamp_response(handler(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """为异步模型响应写入创建时间。"""
        return _stamp_response(await handler(request))
