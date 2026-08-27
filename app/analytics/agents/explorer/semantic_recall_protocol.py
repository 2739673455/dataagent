"""Explorer 语义召回的持久化消息协议"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import ToolMessage

from app.metadata.models.recall import SemanticRecallRecord

type SemanticRecallView = Literal["search_response", "record"]

_REFERENCE_TOOL_VIEWS: dict[str, SemanticRecallView] = {
    "search_context": "search_response",
    "get_recall": "record",
    "merge_recalls": "record",
}


@dataclass(frozen=True, slots=True)
class SemanticRecallReference:
    """描述持久化语义召回记录及其展开视图"""

    query: str
    view: SemanticRecallView


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
    expected_view = _REFERENCE_TOOL_VIEWS.get(message.name or "")
    if expected_view is None or not isinstance(message.content, str):
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
    if not isinstance(query, str) or not query.strip():
        return None
    return SemanticRecallReference(query=query.strip(), view=expected_view)
