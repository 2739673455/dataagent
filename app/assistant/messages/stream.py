"""Planner 模型消息增量解析。"""

from collections.abc import Iterator, Mapping
from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.assistant.messages.content import reasoning_text, text_content


class MessageDelta(TypedDict):
    """一次正文或思考增量及其所属消息的重置标志。"""

    message_id: str
    delta: str
    reset: bool


class MessageDeltaParser:
    """在一次流生命周期内分别跟踪正文和思考，调用方决定事件输出方式。"""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def parse(
        self, data: object
    ) -> Iterator[tuple[Literal["thinking", "text"], MessageDelta]]:
        """忽略非模型增量；同一消息的两类增量分别首次重置。"""
        if not isinstance(data, tuple) or len(data) != 2:
            return
        message, _metadata = data
        if not isinstance(message, AIMessageChunk) or message.id is None:
            return
        message_id = str(message.id)
        parts: tuple[tuple[Literal["thinking", "text"], str | None], ...] = (
            ("thinking", reasoning_text(message)),
            ("text", text_content(message.content)),
        )
        for kind, text in parts:
            if text is None or (kind == "text" and not text):
                continue
            key = (kind, message_id)
            reset = key not in self._seen
            self._seen.add(key)
            yield kind, MessageDelta(message_id=message_id, delta=text, reset=reset)


def update_messages(data: object) -> Iterator[AIMessage | ToolMessage]:
    """从 Planner 节点更新中提取完整模型与工具消息。"""
    if not isinstance(data, Mapping):
        return
    for node in ("model", "tools"):
        update = data.get(node)
        messages = update.get("messages") if isinstance(update, Mapping) else None
        if isinstance(messages, list):
            yield from (m for m in messages if isinstance(m, AIMessage | ToolMessage))
