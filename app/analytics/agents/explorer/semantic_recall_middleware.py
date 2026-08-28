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

from app.analytics.agents.explorer.recall_runtime import (
    create_authorized_semantic_recall_service,
    resolve_semantic_recall_identity,
    semantic_recall_repository,
)
from app.analytics.agents.explorer.semantic_recall_protocol import (
    SemanticRecallReference,
    SemanticRecallView,
    parse_semantic_recall_reference,
)
from app.metadata.models.recall import SemanticRecallRecord
from app.metadata.services.recall import SemanticQueriesNotFoundError


def _expanded_content(
    record: SemanticRecallRecord,
    view: SemanticRecallView,
) -> str:
    """按请求视图序列化已授权的语义召回记录"""
    if view == "resources":
        payload = record.response.model_dump(mode="json")
        payload.pop("recall_id", None)
        payload["query"] = record.query
        payload["query_experiences"] = [
            item.model_dump(mode="json") for item in record.query_experiences
        ]
        payload["query_experiences_retrieved_at"] = (
            record.query_experiences_retrieved_at.isoformat()
        )
    else:
        recall = record.model_dump(mode="json", exclude={"source_queries"})
        recall["response"].pop("recall_id", None)
        payload = {
            "status": "success",
            "recall": recall,
        }
    return json.dumps(payload, ensure_ascii=False)


def _current_turn_references(
    messages: list[Any],
) -> list[tuple[int, SemanticRecallReference]]:
    """提取当前用户回合产生的语义召回引用"""
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
        """拒绝需要异步数据读取的同步模型调用"""
        if _current_turn_references(request.messages):
            raise RuntimeError("语义召回展开需要异步执行")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """在异步模型调用前展开当前回合的语义召回引用"""
        references = _current_turn_references(request.messages)
        if not references:
            return await handler(request)

        messages = list(request.messages)
        try:
            user_id, conversation_id = resolve_semantic_recall_identity(get_config())
            async with semantic_recall_repository() as repo:
                service = await create_authorized_semantic_recall_service(user_id, repo)
                records: dict[str, SemanticRecallRecord] = {}
                for _, reference in references:
                    if reference.query not in records:
                        records[reference.query] = await service.get(
                            user_id,
                            conversation_id,
                            reference.query,
                        )
        except SemanticQueriesNotFoundError as exc:
            expanded_error = json.dumps(
                {
                    "status": "error",
                    "message": "未找到指定的语义召回记录",
                    "queries": exc.queries,
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
            logger.exception("语义召回展开失败")
            expanded_error = json.dumps(
                {
                    "status": "error",
                    "message": "语义召回记录暂不可用",
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
                            records[reference.query],
                            reference.view,
                        )
                    }
                )
        return await handler(request.override(messages=messages))
