"""召回引用的授权加载与公开消息展开，不修改持久化消息。"""

import json
from typing import Any
from uuid import UUID

from langchain_core.messages import ToolMessage
from loguru import logger

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.agents.explorer.semantic_recall_protocol import (
    SemanticRecallReference,
    parse_semantic_recall_references,
    semantic_recall_payload,
)
from app.metadata.models.recall import SemanticRecallRecord


async def load_recall_records(
    user_id: int,
    conversation_id: UUID,
    references: list[tuple[int, tuple[SemanticRecallReference, ...]]],
    recall: SemanticRecallRuntime,
) -> tuple[dict[str, SemanticRecallRecord], set[str]]:
    """批量加载不同 query 的最新记录，缺失项独立处理。"""
    queries = list(
        dict.fromkeys(
            reference.query
            for _, items in references
            for reference in items
            if not reference.deleted
        )
    )
    if not queries:
        return {}, set()
    async with recall.context_service(user_id) as service:
        records = await service.get_many(user_id, conversation_id, queries)
    return records, set(queries) - records.keys()


def replace_reference_content(
    messages: list[Any],
    references: list[tuple[int, tuple[SemanticRecallReference, ...]]],
    records: dict[str, SemanticRecallRecord],
    missing_queries: set[str],
    *,
    unavailable: bool = False,
) -> list[Any]:
    """分别使用已授权内容或失效错误替换消息副本中的引用。"""
    expanded = list(messages)
    for index, items in references:
        message = expanded[index]
        payloads: list[dict[str, Any]] = []
        for reference in items:
            if reference.deleted:
                payload = {"status": "deleted", "query": reference.query}
            elif unavailable:
                payload = {"status": "error", "message": "语义召回记录暂不可用"}
            elif reference.query in missing_queries:
                payload = {
                    "status": "error",
                    "message": "未找到指定的语义召回记录",
                    "queries": [reference.query],
                }
            else:
                payload = semantic_recall_payload(records[reference.query])
            payloads.append(payload)
        content = json.dumps(
            {"status": "success", "recalls": payloads}
            if message.name == "delete_recalls"
            else payloads[0],
            ensure_ascii=False,
        )
        expanded[index] = message.model_copy(update={"content": content})
    return expanded


async def expand_semantic_recall_messages_for_display(
    messages: list[Any],
    user_id: int,
    conversation_id: UUID,
    *,
    recall: SemanticRecallRuntime,
) -> list[Any]:
    """在公开消息投影中展开语义召回引用，不修改持久化消息。"""
    references = [
        (index, reference)
        for index, message in enumerate(messages)
        if isinstance(message, ToolMessage)
        and (reference := parse_semantic_recall_references(message)) is not None
    ]
    if not references:
        return messages
    try:
        records, missing_queries = await load_recall_records(
            user_id,
            conversation_id,
            references,
            recall,
        )
    except Exception:  # noqa: BLE001
        logger.exception("公开消息中的语义召回展开失败")
        return messages
    return replace_reference_content(
        messages,
        references,
        records,
        missing_queries,
    )
