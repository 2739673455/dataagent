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
    parse_semantic_recall_reference,
)
from app.metadata.models.recall import SemanticRecallRecord
from app.metadata.services.recall import SemanticQueriesNotFoundError


def _expanded_content(
    record: SemanticRecallRecord,
) -> str:
    """仅序列化模型执行 SQL 所需的元数据和历史经验"""
    resources, query_experiences = _model_visible_recall(record)
    payload = {**resources, "query_experiences": query_experiences}
    return json.dumps(payload, ensure_ascii=False)


def _model_visible_recall(
    record: SemanticRecallRecord,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按模型可用的元数据字段构造显式白名单投影"""
    response = record.response
    resources = {
        "metrics": [
            {
                "name": item.name,
                "description": item.description,
                "alias": item.alias,
                "relevant_columns": item.relevant_columns,
            }
            for item in response.metrics
        ],
        "columns": [
            {
                "t_name": item.t_name,
                "name": item.name,
                "type": item.type,
                "description": item.description,
                "alias": item.alias,
                "examples": item.examples,
                "reference_t_name": item.reference_t_name,
                "reference_c_name": item.reference_c_name,
            }
            for item in response.columns
        ],
        "values": [
            {
                "value": item.value,
                "t_name": item.t_name,
                "c_name": item.c_name,
            }
            for item in response.values
        ],
        "tables": [
            {
                "name": item.name,
                "role": item.role,
                "description": item.description,
                "primary_key_columns": item.primary_key_columns,
            }
            for item in response.tables
        ],
    }
    query_experiences = [
        {
            "purpose": experience.purpose,
            "sql_template": experience.sql_template,
            "dialect": experience.dialect,
            "assets": [
                {
                    "kind": asset.kind,
                    "database": asset.database,
                    "table": asset.table,
                    "column": asset.column,
                }
                for asset in experience.assets
            ],
        }
        for experience in record.query_experiences
    ]
    return resources, query_experiences


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
                        )
                    }
                )
        return await handler(request.override(messages=messages))
