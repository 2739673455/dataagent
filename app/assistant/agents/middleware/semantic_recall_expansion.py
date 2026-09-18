"""语义召回引用的模型请求临时展开。"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_config
from loguru import logger

from app.assistant.agents.explorer.recall_runtime import (
    SemanticRecallRuntime,
    resolve_semantic_recall_identity,
)
from app.assistant.agents.explorer.semantic_recall_messages import (
    load_recall_records,
    replace_reference_content,
)
from app.assistant.agents.explorer.semantic_recall_protocol import (
    SemanticRecallReference,
    parse_semantic_recall_references,
)


def _current_turn_references(
    messages: list[Any],
) -> list[tuple[int, tuple[SemanticRecallReference, ...]]]:
    """提取当前用户回合产生的语义召回引用。"""
    last_human_index = -1
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and not message.additional_kwargs.get(
            "dataagent_internal_retry"
        ):
            last_human_index = index
    return [
        (index, reference)
        for index, message in enumerate(messages)
        if index > last_human_index
        and isinstance(message, ToolMessage)
        and (reference := parse_semantic_recall_references(message)) is not None
    ]


class SemanticRecallExpansionMiddleware(AgentMiddleware[Any, Any, Any]):
    """仅在当前模型请求中展开已授权的召回记录。"""

    def __init__(self, recall: SemanticRecallRuntime) -> None:
        """绑定当前进程的召回资源。"""
        self._recall = recall

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """拒绝需要异步数据读取的同步模型调用。"""
        if _current_turn_references(request.messages):
            raise RuntimeError("语义召回展开需要异步执行")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """在异步模型调用前展开当前回合的语义召回引用。"""
        references = _current_turn_references(request.messages)
        if not references:
            return await handler(request)

        messages = list(request.messages)
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(get_config())
            records, missing_queries = await load_recall_records(
                user_id,
                conversation_id,
                references,
                self._recall,
            )
        except Exception:  # noqa: BLE001
            logger.exception("语义召回展开失败")
            messages = replace_reference_content(
                messages, references, {}, set(), unavailable=True
            )
            return await handler(request.override(messages=messages))

        messages = replace_reference_content(
            messages,
            references,
            records,
            missing_queries,
        )
        return await handler(request.override(messages=messages))
