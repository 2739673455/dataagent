import json
from datetime import datetime
from typing import Any, cast

from app.entities.chat import Message
from app.schemas import chat_schema
from langchain.messages import AIMessage, ToolMessage


def _parts_from_ai_message(message: AIMessage) -> list[chat_schema.MessagePart]:
    """将 AIMessage 转换为 MessageSchema 的消息片段列表"""
    parts: list[chat_schema.MessagePart] = []
    content = message.content

    # 处理 content
    assert isinstance(content, str), "AI message content is not string"
    if content.strip():
        parts.append(chat_schema.TextContent(text=content))

    # 处理 tool call
    for tool_call in message.tool_calls:
        parts.append(
            chat_schema.ToolCallPart(
                tool_call_id=tool_call.get("id") or "",
                name=tool_call.get("name") or "",
                args=tool_call.get("args", {}),
            )
        )

    return parts


def entity_to_schema(message: Message) -> chat_schema.MessageSchema:
    """将消息实体转换为 MessageSchema"""
    parts: list[chat_schema.MessagePart] = []
    for item in json.loads(message.parts):
        schema = {
            "text": chat_schema.TextContent,
            "image_url": chat_schema.ImageContent,
            "tool_call": chat_schema.ToolCallPart,
            "tool_result": chat_schema.ToolResultPart,
        }.get(item["type"])
        if schema is None:
            raise ValueError(f"Unsupported message part type: {item['type']}")
        parts.append(schema(**item))

    attachments = (
        [chat_schema.Attachment(**item) for item in json.loads(message.attachments)]
        if message.attachments
        else None
    )

    return chat_schema.MessageSchema(
        message_id=message.id,
        role=cast(chat_schema.MessageRole, message.role),
        parts=parts,
        attachments=attachments,
        finish_reason=cast(chat_schema.FinishReason | None, message.finish_reason),
        timestamp=message.create_at,
    )


def schema_to_entity(
    message: chat_schema.MessageSchema, conversation_id: int
) -> Message:
    """将 MessageSchema 转换为消息实体"""
    parts = json.dumps(
        [part.model_dump() for part in message.parts], ensure_ascii=False
    )

    attachments = (
        json.dumps(
            [attachment.model_dump() for attachment in message.attachments],
            ensure_ascii=False,
        )
        if message.attachments is not None
        else None
    )

    entity = Message(
        conversation_id=conversation_id,
        role=message.role,
        parts=parts,
        attachments=attachments,
        finish_reason=message.finish_reason,
    )

    if message.message_id is not None:
        entity.id = message.message_id
    if message.timestamp is not None:
        entity.create_at = message.timestamp

    return entity


def langchain_message_to_schema(
    message: AIMessage | ToolMessage,
) -> chat_schema.MessageSchema | None:
    """将 LangChain 消息转换为 MessageSchema，同时添加时间戳"""
    timestamp = datetime.now()

    # 处理 AIMessage
    if isinstance(message, AIMessage):
        return chat_schema.MessageSchema(
            role="assistant",
            parts=_parts_from_ai_message(message),
            finish_reason=message.response_metadata.get("finish_reason"),
            timestamp=timestamp,
        )

    # 处理 ToolMessage
    elif isinstance(message, ToolMessage):
        return chat_schema.MessageSchema(
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    tool_call_id=message.tool_call_id,
                    name=message.name or "",
                    content=str(message.content),
                )
            ],
            finish_reason=None,
            timestamp=timestamp,
        )

    else:
        return None


def agent_chunk_to_schemas(chunk: dict) -> list[chat_schema.MessageSchema]:
    """将 Agent 流式输出块转换为 MessageSchema 列表"""
    schemas: list[chat_schema.MessageSchema] = []

    for key in ("model", "tools"):
        if (
            (key in chunk)
            and (messages := chunk[key].get("messages"))
            and (isinstance(messages, list))
        ):
            for message in messages:
                if schema := langchain_message_to_schema(message):
                    schemas.append(schema)

    return schemas


def schema_to_langchain_message(message: chat_schema.MessageSchema) -> dict[str, Any]:
    """将 MessageSchema 转换为 LangChain 消息"""

    # 工具消息
    if message.role == "tool":
        tool_result = next(
            part
            for part in message.parts
            if isinstance(part, chat_schema.ToolResultPart)
        )
        return {
            "role": "tool",
            "tool_call_id": tool_result.tool_call_id,
            "name": tool_result.name,
            "content": tool_result.content,
        }

    # 用户或模型消息
    content_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        elif isinstance(part, chat_schema.ToolCallPart):
            tool_calls.append(
                {
                    "type": "tool_call",
                    "id": part.tool_call_id,
                    "name": part.name,
                    "args": part.args,
                }
            )

    payload: dict[str, Any] = {"role": message.role, "content": content_parts}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload
