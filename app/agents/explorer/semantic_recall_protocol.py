"""Explorer 语义召回的持久化消息协议"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import ToolMessage

from app.models.semantic_recall import SemanticRecallRecord

type SemanticRecallView = Literal["search_response", "record"]

_REFERENCE_TOOL_VIEWS: dict[str, SemanticRecallView] = {
    "search_semantic_resources": "search_response",
    "get_semantic_recall": "record",
    "merge_semantic_recalls": "record",
}


@dataclass(frozen=True, slots=True)
class SemanticRecallReference:
    recall_id: str
    view: SemanticRecallView


def semantic_recall_reference(
    record: SemanticRecallRecord,
) -> dict[str, Any]:
    """构造只含持久化记录引用的工具结果"""
    return {
        "status": "stored",
        "recall_id": record.recall_id,
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
    recall_id = payload.get("recall_id")
    if not isinstance(recall_id, str) or not recall_id.strip():
        return None
    return SemanticRecallReference(recall_id=recall_id, view=expected_view)
