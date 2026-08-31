"""Explorer 语义召回的持久化消息协议"""

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage

from app.metadata.models.recall import SemanticRecallRecord

_REFERENCE_TOOLS = frozenset({"recall_context", "get_recall", "merge_recalls"})


@dataclass(frozen=True, slots=True)
class SemanticRecallReference:
    """描述一条待展开的持久化语义召回记录"""

    query: str


def semantic_recall_reference(
    record: SemanticRecallRecord,
) -> dict[str, Any]:
    """构造只含持久化记录引用的工具结果"""
    return {
        "status": "stored",
        "query": record.query,
    }


def parse_semantic_recall_reference(
    message: ToolMessage,
) -> SemanticRecallReference | None:
    """解析受控的语义召回引用消息"""
    if message.name not in _REFERENCE_TOOLS or not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "stored":
        return None
    query = payload.get("query")
    if not isinstance(query, str) or not query or query != query.strip():
        return None
    return SemanticRecallReference(query=query)
