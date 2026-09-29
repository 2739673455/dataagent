"""将用户消息接收时间投影到模型请求。"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, HumanMessage


class MessageContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """向模型提供用户消息时间，不影响前端展示。"""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步投影用户消息时间。"""
        return handler(request.override(messages=_project_messages(request.messages)))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步投影用户消息时间。"""
        return await handler(
            request.override(messages=_project_messages(request.messages))
        )


def _project_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    """向用户消息添加时间提示，不改写持久化消息。"""
    projected: list[AnyMessage] = []
    for message in messages:
        received_at = message.additional_kwargs.get("received_at")
        if isinstance(message, HumanMessage) and isinstance(received_at, str):
            content = (
                [{"type": "text", "text": message.content}]
                if isinstance(message.content, str)
                else list(message.content)
            )
            timestamp = {
                "type": "text",
                "text": f"当前时间：{received_at}",
            }
            projected.append(
                message.model_copy(update={"content": [timestamp, *content]})
            )
            continue
        projected.append(message)
    return projected
