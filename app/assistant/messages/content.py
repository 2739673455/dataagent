"""LangChain 消息内容读取。"""

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

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


def message_text(message: BaseMessage) -> str | None:
    """读取消息正文文本或流式正文增量。"""
    return text_content(message.content)


def reasoning_text(message: BaseMessage) -> str | None:
    """合并模型消息中的思考文本。"""

    def block_text(block: Mapping[str, Any]) -> str | None:
        """读取直接保存的思考文本，或拼接嵌套的 reasoning_text 内容块。"""
        reasoning = block.get("reasoning")
        if isinstance(reasoning, str):
            return reasoning
        content = block.get("content")
        if not isinstance(content, list):
            return None
        return (
            "".join(
                item["text"]
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "reasoning_text"
                and isinstance(item.get("text"), str)
            )
            or None
        )

    # 原始 content 包含完整 reasoning；content_blocks 规范化可能将其移入 extras。
    parts: list[str] = []
    for block in normalized_content_blocks(message.content):
        if not isinstance(block, dict) or block.get("type") != "reasoning":
            continue
        if text := block_text(block):
            parts.append(text)
    if parts:
        return "".join(parts)

    # Chat Completions 的 additional_kwargs 思考内容由 content_blocks 提供。
    for block in message.content_blocks:
        if block.get("type") == "reasoning" and (text := block_text(block)):
            parts.append(text)
    return "".join(parts) or None


def is_final_assistant_message(message: BaseMessage) -> bool:
    """判断消息是否为可交付附件的完整 Agent 终答。"""
    if (
        not isinstance(message, AIMessage)
        or message.tool_calls
        or message.invalid_tool_calls
    ):
        return False
    finish_reason = message.response_metadata.get("finish_reason")
    return finish_reason in {None, "stop"}
