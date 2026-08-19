"""语义召回引用的模型请求临时展开"""

import json
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

from app.agents.explorer.recall_runtime import (
    create_authorized_semantic_recall_service,
    resolve_semantic_recall_context,
)
from app.agents.explorer.semantic_recall_protocol import (
    SemanticRecallReference,
    SemanticRecallView,
    parse_semantic_recall_reference,
)
from app.models.semantic_recall import SemanticRecallRecord
from app.services.semantic_recall_service import SemanticRecallsNotFoundError


def _expanded_content(
    record: SemanticRecallRecord,
    view: SemanticRecallView,
) -> str:
    if view == "search_response":
        payload = record.response.model_dump(mode="json")
        payload["recall_id"] = record.recall_id
    else:
        payload = {
            "status": "success",
            "recall": record.model_dump(mode="json"),
        }
    return json.dumps(payload, ensure_ascii=False)


def _current_turn_references(
    messages: list[Any],
) -> list[tuple[int, SemanticRecallReference]]:
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
        and (reference := parse_semantic_recall_reference(message)) is not None
    ]


class SemanticRecallExpansionMiddleware(AgentMiddleware[Any, Any, Any]):
    """仅在当前模型请求中展开已授权的召回记录"""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        if _current_turn_references(request.messages):
            raise RuntimeError("semantic recall expansion requires async execution")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        references = _current_turn_references(request.messages)
        if not references:
            return await handler(request)

        messages = list(request.messages)
        try:
            user_id, conversation_id, repo = resolve_semantic_recall_context(
                get_config(),
                request.runtime.store,
            )
            service = await create_authorized_semantic_recall_service(user_id, repo)
            records: dict[str, SemanticRecallRecord] = {}
            for _, reference in references:
                if reference.recall_id not in records:
                    records[reference.recall_id] = await service.get(
                        user_id,
                        conversation_id,
                        reference.recall_id,
                    )
        except SemanticRecallsNotFoundError as exc:
            expanded_error = json.dumps(
                {
                    "status": "error",
                    "message": "semantic recall not found",
                    "recall_ids": exc.recall_ids,
                },
                ensure_ascii=False,
            )
            for index, _ in references:
                message = messages[index]
                if isinstance(message, ToolMessage):
                    messages[index] = message.model_copy(
                        update={"content": expanded_error}
                    )
            return await handler(request.override(messages=messages))
        except Exception:  # noqa: BLE001
            logger.exception("Semantic recall expansion failed")
            expanded_error = json.dumps(
                {
                    "status": "error",
                    "message": "Semantic recall is temporarily unavailable",
                },
                ensure_ascii=False,
            )
            for index, _ in references:
                message = messages[index]
                if isinstance(message, ToolMessage):
                    messages[index] = message.model_copy(
                        update={"content": expanded_error}
                    )
            return await handler(request.override(messages=messages))

        for index, reference in references:
            message = messages[index]
            if isinstance(message, ToolMessage):
                messages[index] = message.model_copy(
                    update={
                        "content": _expanded_content(
                            records[reference.recall_id],
                            reference.view,
                        )
                    }
                )
        return await handler(request.override(messages=messages))
