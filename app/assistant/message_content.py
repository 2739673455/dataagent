"""LangChain 消息内容读取。"""

from collections.abc import Mapping
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


def message_text(message: BaseMessage) -> str | None:
    """读取消息正文文本或流式正文增量。"""
    return text_content(message.content)


def reasoning_text(message: BaseMessage) -> str | None:
    """合并模型消息中的思考文本。"""
    def block_text(block: Mapping[str, Any]) -> str | None:
        reasoning = block.get("reasoning")
        if isinstance(reasoning, str):
            return reasoning
        content = block.get("content")
        if not isinstance(content, list):
            return None
        return "".join(
            item["text"]
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "reasoning_text"
            and isinstance(item.get("text"), str)
        ) or None

    # Responses Provider 的扩展字段会让 LangChain 把 reasoning item.content
    # 移入 content_blocks[].extras。Checkpoint 仍完整保存原始 content，因此优先
    # 从原始块读取，避免模型请求投影依赖 LangChain 的有损标准化结果。
    parts: list[str] = []
    for block in normalized_content_blocks(message.content):
        if not isinstance(block, dict) or block.get("type") != "reasoning":
            continue
        if text := block_text(block):
            parts.append(text)
    if parts:
        return "".join(parts)

    # Chat Completions 等 Provider 可能只通过 additional_kwargs 暴露思考，
    # LangChain 会将其规范化到 content_blocks，保留该路径作为回退。
    for block in message.content_blocks:
        if block.get("type") == "reasoning" and (text := block_text(block)):
            parts.append(text)
    return "".join(parts) or None
