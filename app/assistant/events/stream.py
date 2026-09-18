"""Planner 与 Specialist 共用的模型消息增量解析。"""

from collections.abc import Iterator
from typing import Literal, TypedDict

from langchain_core.messages import AIMessageChunk

from app.assistant.events.content import message_text, reasoning_text


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
            ("text", message_text(message)),
        )
        for kind, text in parts:
            if text is None or (kind == "text" and not text):
                continue
            key = (kind, message_id)
            reset = key not in self._seen
            self._seen.add(key)
            yield kind, MessageDelta(message_id=message_id, delta=text, reset=reset)
