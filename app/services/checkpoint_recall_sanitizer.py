"""语义召回检查点载荷清理"""

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

_SEMANTIC_RECALL_RESULT_TOOLS = {
    "search_semantic_resources",
    "get_semantic_recall",
    "list_semantic_recalls",
    "merge_semantic_recalls",
}


@runtime_checkable
class CheckpointGraph(Protocol):
    """支持状态读取和局部更新的检查点图"""

    async def aget_state(self, config: RunnableConfig) -> Any: ...

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, Any],
    ) -> Any: ...


def _semantic_recall_ids(payload: dict[str, Any]) -> list[str]:
    """从语义召回工具结果中提取记录 ID"""
    recall_ids: list[str] = []
    direct_id = payload.get("recall_id")
    if isinstance(direct_id, str):
        recall_ids.append(direct_id)

    recall = payload.get("recall")
    if isinstance(recall, dict) and isinstance(recall.get("recall_id"), str):
        recall_ids.append(recall["recall_id"])

    recalls = payload.get("recalls")
    if isinstance(recalls, list):
        recall_ids.extend(
            item["recall_id"]
            for item in recalls
            if isinstance(item, dict) and isinstance(item.get("recall_id"), str)
        )
    return list(dict.fromkeys(recall_ids))


def compact_semantic_recall_message(message: ToolMessage) -> ToolMessage | None:
    """将完整召回工具历史压缩为可重新读取的记录引用"""
    if message.name not in _SEMANTIC_RECALL_RESULT_TOOLS:
        return None
    if not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None

    recall_ids = _semantic_recall_ids(payload)
    if not recall_ids:
        return None
    compact_content = json.dumps(
        {
            "status": "stored",
            "recall_ids": recall_ids,
            "message": "Use get_semantic_recall to load a stored recall",
        },
        ensure_ascii=False,
    )
    if compact_content == message.content:
        return None
    return message.model_copy(update={"content": compact_content})


async def compact_semantic_recall_state(
    graph: object,
    config: RunnableConfig,
) -> int:
    """清除一个图检查点中的完整语义召回工具载荷"""
    if not isinstance(graph, CheckpointGraph):
        return 0
    state = await graph.aget_state(config)
    values = getattr(state, "values", None)
    if not isinstance(values, Mapping):
        return 0
    messages = values.get("messages", [])
    if not isinstance(messages, list):
        return 0
    replacements = [
        compact
        for message in messages
        if isinstance(message, ToolMessage)
        if (compact := compact_semantic_recall_message(message)) is not None
    ]
    if replacements:
        await graph.aupdate_state(config, {"messages": replacements})
    return len(replacements)
