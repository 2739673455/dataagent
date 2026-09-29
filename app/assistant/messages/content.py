"""LangChain 消息内容读取。"""

from typing import Any

from langchain_core.messages import BaseMessage

_TEXT_BLOCK_TYPES = frozenset({"text", "input_text", "output_text"})


def normalized_content_blocks(content: Any) -> list[str | dict[str, Any]]:
    """保留消息中可投影的文本与结构化内容块。"""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, (str, dict))]
    return [str(content)]


def text_content(content: Any) -> str | None:
    """合并字符串内容和 LangChain 标准文本内容块。"""
    parts: list[str] = []
    for block in normalized_content_blocks(content):
        if isinstance(block, str):
            parts.append(block)
        elif (
            isinstance(block, dict)
            and block.get("type") in _TEXT_BLOCK_TYPES
            and isinstance(block.get("text"), str)
        ):
            parts.append(block["text"])
    return "".join(parts) or None


def reasoning_text(message: BaseMessage) -> str | None:
    """读取 Chat Completions 思考字段或标准思考内容块。"""
    if isinstance(reasoning := message.additional_kwargs.get("reasoning_content"), str):
        return reasoning or None
    return (
        "".join(
            text
            for block in message.content_blocks
            if block.get("type") == "reasoning"
            and isinstance(text := block.get("reasoning"), str)
        )
        or None
    )
