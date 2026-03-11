import json
from typing import Any, cast

from app.entities.chat import Message
from app.schemas import chat_schema
from langchain.messages import AIMessage, ToolMessage


def _parse_part(item: dict) -> chat_schema.MessagePart:
    """将字典解析为 MessageSchema 的消息片段"""
    match item["type"]:
        case "text":
            return chat_schema.TextContent(**item)
        case "image_url":
            return chat_schema.ImageContent(**item)
        case "tool_call":
            return chat_schema.ToolCallPart(**item)
        case "tool_result":
            return chat_schema.ToolResultPart(**item)
        case _:
            raise ValueError(f"Unsupported message part type: {item['type']}")


def _parts_from_ai_message(message: AIMessage) -> list[chat_schema.MessagePart]:
    """将 AIMessage 转换为 MessageSchema 的消息片段列表"""
    parts: list[chat_schema.MessagePart] = []
    content = message.content

    if isinstance(content, str):
        if content:
            parts.append(chat_schema.TextContent(text=content))
    else:
        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "text":
                parts.append(chat_schema.TextContent(**item))
            elif item_type == "image_url":
                parts.append(chat_schema.ImageContent(**item))

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
    parts = [_parse_part(item) for item in json.loads(message.parts)]
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
        timestamp=message.create_at,
    )


def langchain_message_to_schema(
    message: AIMessage | ToolMessage,
) -> chat_schema.MessageSchema | None:
    """将 LangChain 消息转换为 MessageSchema"""
    if isinstance(message, AIMessage):
        return chat_schema.MessageSchema(
            role="assistant",
            parts=_parts_from_ai_message(message),
        )

    if isinstance(message, ToolMessage):
        return chat_schema.MessageSchema(
            role="tool",
            parts=[
                chat_schema.ToolResultPart(
                    tool_call_id=message.tool_call_id or "",
                    name=message.name or "",
                    content=str(message.content),
                )
            ],
        )

    return None


def agent_chunk_to_schema(chunk: dict) -> chat_schema.MessageSchema | None:
    """将 Agent 流式输出块转换为 MessageSchema"""
    if "model" in chunk:
        model_messages = chunk["model"]["messages"]
        ai_message = model_messages[-1]
        if not isinstance(ai_message, AIMessage):
            return None
        return langchain_message_to_schema(ai_message)

    if "tools" in chunk:
        tool_messages = chunk["tools"]["messages"]
        tool_message = tool_messages[-1]
        if not isinstance(tool_message, ToolMessage):
            return None
        return langchain_message_to_schema(tool_message)

    return None


def schema_to_langchain_message(message: chat_schema.MessageSchema) -> dict[str, Any]:
    """将 MessageSchema 转换为 LangChain 消息"""
    if message.role == "tool":
        tool_result = next(
            part
            for part in message.parts
            if isinstance(part, chat_schema.ToolResultPart)
        )
        return {
            "role": message.role,
            "content": tool_result.content,
            "tool_call_id": tool_result.tool_call_id,
            "name": tool_result.name,
        }

    content_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    for part in message.parts:
        if isinstance(part, (chat_schema.TextContent, chat_schema.ImageContent)):
            content_parts.append(part.model_dump())
        elif isinstance(part, chat_schema.ToolCallPart):
            tool_calls.append(
                {
                    "id": part.tool_call_id,
                    "name": part.name,
                    "args": part.args,
                    "type": "tool_call",
                }
            )

    payload: dict[str, Any] = {"role": message.role, "content": content_parts}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def schema_to_entity(
    message: chat_schema.MessageSchema, conversation_id: int
) -> Message:
    """将 MessageSchema 转换为消息实体"""
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
        parts=json.dumps(
            [part.model_dump() for part in message.parts], ensure_ascii=False
        ),
        attachments=attachments,
    )

    if message.message_id is not None:
        entity.id = message.message_id
    if message.timestamp is not None:
        entity.create_at = message.timestamp

    return entity
