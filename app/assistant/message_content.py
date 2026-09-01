"""LangChain 消息内容读取。"""

from langchain_core.messages import BaseMessage


def reasoning_text(message: BaseMessage) -> str | None:
    """合并模型消息中的标准思考内容块。"""
    parts: list[str] = []
    for block in message.content_blocks:
        reasoning = block.get("reasoning")
        if block.get("type") == "reasoning" and isinstance(reasoning, str):
            parts.append(reasoning)
    return "".join(parts) or None
