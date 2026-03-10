import json
from datetime import datetime
from typing import Annotated, Literal, cast

from app.entities.chat import Message
from langchain.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

# 对话


class ConversationResponse(BaseModel):
    conversation_id: int
    title: str
    update_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]


class UpdateConversationRequest(BaseModel):
    conversation_id: int = Field(..., description="对话ID")
    title: str = Field(..., description="对话标题")


class DeleteConversationRequest(BaseModel):
    conversation_ids: list[int] = Field(..., description="对话ID列表")


# 消息


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str = Field(..., description="文本内容")


class ImageContent(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: str = Field(..., description="图片链接")


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    args: dict = Field(default_factory=dict, description="工具参数")


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    content: str = Field(..., description="工具执行结果")


MessageRole = Literal["user", "assistant", "tool", "system"]


MessagePart = Annotated[
    TextContent | ImageContent | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Attachment(BaseModel):
    name: str = Field(..., description="附件名称")
    url: str = Field(..., description="附件链接")


class MessageItem(BaseModel):
    message_id: int | None = Field(default=None, description="消息ID")
    role: MessageRole = Field(..., description="发送者")
    parts: list[MessagePart] = Field(..., description="消息片段")
    attachments: list[Attachment] | None = Field(default=None, description="附件列表")
    timestamp: datetime | None = Field(default=None, description="发送时间")

    @classmethod
    def from_entity(cls, message: Message) -> "MessageItem":
        parts = [cls._parse_part(item) for item in json.loads(message.parts)]

        attachments = (
            [Attachment(**item) for item in json.loads(message.attachments)]
            if message.attachments
            else None
        )

        return cls(
            message_id=message.id,
            role=cast(MessageRole, message.role),
            parts=parts,
            attachments=attachments,
            timestamp=message.create_at,
        )

    @staticmethod
    def _parse_part(item: dict) -> MessagePart:
        match item["type"]:
            case "text":
                return TextContent(**item)
            case "image_url":
                return ImageContent(**item)
            case "tool_call":
                return ToolCallPart(**item)
            case "tool_result":
                return ToolResultPart(**item)
            case _:
                raise ValueError(f"Unsupported message part type: {item['type']}")

    @classmethod
    def from_agent_chunk(cls, chunk: dict) -> "MessageItem | None":
        if "model" in chunk:
            model_messages = chunk["model"]["messages"]
            ai_message = model_messages[-1]
            if not isinstance(ai_message, AIMessage):
                return None

            return cls.from_langchain_message(ai_message)

        if "tools" in chunk:
            tool_messages = chunk["tools"]["messages"]
            tool_message = tool_messages[-1]
            if not isinstance(tool_message, ToolMessage):
                return None

            return cls.from_langchain_message(tool_message)

        return None

    @classmethod
    def from_langchain_message(
        cls,
        message: AIMessage | ToolMessage,
    ) -> "MessageItem | None":
        if isinstance(message, AIMessage):
            return cls(
                role="assistant",
                parts=cls._parts_from_ai_message(message),
            )

        if isinstance(message, ToolMessage):
            return cls(
                role="tool",
                parts=[
                    ToolResultPart(
                        tool_call_id=message.tool_call_id or "",
                        name=message.name or "",
                        content=str(message.content),
                    )
                ],
            )

        return None

    @staticmethod
    def _parts_from_ai_message(message: AIMessage) -> list[MessagePart]:
        parts: list[MessagePart] = []
        content = message.content

        if isinstance(content, str):
            if content:
                parts.append(TextContent(text=content))
        else:
            for item in content:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type")
                if item_type == "text":
                    parts.append(TextContent(**item))
                elif item_type == "image_url":
                    parts.append(ImageContent(**item))

        for tool_call in message.tool_calls:
            parts.append(
                ToolCallPart(
                    tool_call_id=tool_call.get("id") or "",
                    name=tool_call.get("name") or "",
                    args=tool_call.get("args", {}),
                )
            )

        return parts


class MessageListResponse(BaseModel):
    messages: list[MessageItem]


class WebSocketChatRequest(BaseModel):
    message: MessageItem = Field(..., description="用户消息")


class WebSocketMessageResponse(BaseModel):
    type: Literal["message"] = "message"
    message: MessageItem = Field(..., description="消息内容")


class WebSocketErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    content: str = Field(..., description="错误信息")
