from datetime import datetime
from typing import Annotated, Literal

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


class CreateConversationRequest(BaseModel):
    is_draft: Literal[0, 1] = Field(default=0, description="是否创建草稿对话")


class WebSocketTokenResponse(BaseModel):
    websocket_token: str = Field(..., description="WebSocket 临时令牌")
    expires_in: int = Field(..., description="过期时间（秒）")


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
FinishReason = Literal["stop", "tool_calls"]


MessagePart = Annotated[
    TextContent | ImageContent | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Attachment(BaseModel):
    raw_name: str = Field(..., description="原始附件名称")
    path: str = Field(..., description="工作区相对路径")


class MessageSchema(BaseModel):
    message_id: int | None = Field(default=None, description="消息ID")
    role: MessageRole = Field(..., description="发送者")
    parts: list[MessagePart] = Field(..., description="消息片段")
    attachments: list[Attachment] | None = Field(default=None, description="附件列表")
    finish_reason: FinishReason | None = Field(default=None, description="完成原因")
    timestamp: datetime | None = Field(default=None, description="发送时间")


class MessageListResponse(BaseModel):
    messages: list[MessageSchema]


class WebSocketChatRequest(BaseModel):
    message: MessageSchema = Field(..., description="用户消息")


class WebSocketMessageResponse(BaseModel):
    type: Literal["message"] = "message"
    message: MessageSchema = Field(..., description="消息内容")


class WebSocketErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    content: str = Field(..., description="错误信息")


class UploadAttachmentResponse(BaseModel):
    attachment: Attachment = Field(..., description="上传后的附件信息")


class DeleteAttachmentRequest(BaseModel):
    conversation_id: int = Field(..., description="对话ID")
    path: str = Field(..., description="相对工作区路径")
