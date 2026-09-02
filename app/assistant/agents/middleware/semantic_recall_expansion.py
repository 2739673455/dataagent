"""语义召回引用的模型请求临时展开。"""

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.config import get_config
from loguru import logger

from app.assistant.agents.explorer.recall_runtime import (
    create_authorized_semantic_recall_service,
    resolve_semantic_recall_identity,
    semantic_recall_repository,
)
from app.assistant.agents.explorer.semantic_recall_protocol import (
    SemanticRecallReference,
    parse_semantic_recall_reference,
)
from app.metadata.models.recall import SemanticRecallRecord
from app.metadata.services.recall import SemanticQueriesNotFoundError


def semantic_recall_payload(
    record: SemanticRecallRecord,
) -> dict[str, Any]:
    """投影模型执行 SQL 所需的元数据和历史经验。"""
    response = record.response
    values_by_column: dict[tuple[str, str], list[str]] = {}
    for item in response.values:
        values_by_column.setdefault((item.t_name, item.c_name), []).append(item.value)

    tables: dict[str, dict[str, Any]] = {
        item.name: {
            "role": item.role,
            "description": item.description,
            "primary_key_columns": item.primary_key_columns,
            "columns": {},
        }
        for item in response.tables
    }
    for item in response.columns:
        table = tables.get(item.t_name)
        if table is None:
            continue
        column = {
            "type": item.type,
            "description": item.description,
            "alias": item.alias,
            "examples": item.examples,
            "reference_t_name": item.reference_t_name,
            "reference_c_name": item.reference_c_name,
        }
        values = values_by_column.get((item.t_name, item.name))
        if values:
            column["values"] = values
        table["columns"][item.name] = column

    return {
        "query": record.query,
        "tables": tables,
        "metrics": {
            item.name: {
                "description": item.description,
                "alias": item.alias,
                "relevant_columns": item.relevant_columns,
            }
            for item in response.metrics
        },
        "query_experiences": [
            {
                "id": str(experience.id),
                "purpose": experience.purpose,
                "sql_template": experience.sql_template,
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
        ],
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _expanded_content(record: SemanticRecallRecord) -> str:
    """序列化模型可见的语义召回上下文。"""
    return json.dumps(semantic_recall_payload(record), ensure_ascii=False)


def _current_turn_references(
    messages: list[Any],
) -> list[tuple[int, SemanticRecallReference]]:
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
        and (reference := parse_semantic_recall_reference(message)) is not None
    ]


async def _load_recall_records(
    user_id: int,
    conversation_id: UUID,
    references: list[tuple[int, SemanticRecallReference]],
) -> tuple[dict[str, SemanticRecallRecord], set[str]]:
    """逐个 query 加载召回记录，并隔离已失效的引用。"""
    async with semantic_recall_repository() as repo:
        service = await create_authorized_semantic_recall_service(user_id, repo)
        records: dict[str, SemanticRecallRecord] = {}
        missing_queries: set[str] = set()
        for _, reference in references:
            if reference.query in records or reference.query in missing_queries:
                continue
            try:
                records[reference.query] = await service.get(
                    user_id,
                    conversation_id,
                    reference.query,
                )
            except SemanticQueriesNotFoundError as exc:
                missing_queries.add(reference.query)
                missing_queries.update(exc.queries)
    return records, missing_queries


def _replace_reference_content(
    messages: list[Any],
    references: list[tuple[int, SemanticRecallReference]],
    records: dict[str, SemanticRecallRecord],
    missing_queries: set[str],
) -> list[Any]:
    """分别使用已授权内容或失效错误替换消息副本中的引用。"""
    expanded = list(messages)
    for index, reference in references:
        message = expanded[index]
        if reference.query in missing_queries:
            content = json.dumps(
                {
                    "status": "error",
                    "message": "未找到指定的语义召回记录",
                    "queries": [reference.query],
                },
                ensure_ascii=False,
            )
        else:
            content = _expanded_content(records[reference.query])
        expanded[index] = message.model_copy(update={"content": content})
    return expanded


async def expand_semantic_recall_messages_for_display(
    messages: list[Any],
    user_id: int,
    conversation_id: UUID,
) -> list[Any]:
    """在公开消息投影中展开语义召回引用，不修改持久化消息。"""
    references = [
        (index, reference)
        for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
        and (reference := parse_semantic_recall_reference(message)) is not None
    ]
    if not references:
        return messages
    try:
        records, missing_queries = await _load_recall_records(
            user_id,
            conversation_id,
            references,
        )
    except Exception:  # noqa: BLE001
        logger.exception("公开消息中的语义召回展开失败")
        return messages
    return _replace_reference_content(
        messages,
        references,
        records,
        missing_queries,
    )


class SemanticRecallExpansionMiddleware(AgentMiddleware[Any, Any, Any]):
    """仅在当前模型请求中展开已授权的召回记录。"""

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
            records, missing_queries = await _load_recall_records(
                user_id,
                conversation_id,
                references,
            )
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
                messages[index] = message.model_copy(update={"content": expanded_error})
            return await handler(request.override(messages=messages))

        messages = _replace_reference_content(
            messages,
            references,
            records,
            missing_queries,
        )
        return await handler(request.override(messages=messages))
