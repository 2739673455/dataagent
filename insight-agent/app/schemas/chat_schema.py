from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# 对话


class CreateConversationRequest(BaseModel):
    """创建对话请求"""

    is_draft: Literal[0, 1] = Field(default=0, description="是否创建草稿对话")


class DeleteConversationRequest(BaseModel):
    """删除对话请求"""

    conversation_ids: list[int] = Field(..., description="对话ID列表")


class UpdateConversationRequest(BaseModel):
    """更新对话请求"""

    conversation_id: int = Field(..., description="对话ID")
    title: str = Field(..., description="对话标题")


class ConversationResponse(BaseModel):
    """对话响应"""

    conversation_id: int
    title: str
    update_at: datetime


class ConversationListResponse(BaseModel):
    """对话列表响应"""

    conversations: list[ConversationResponse]


class WebSocketTokenResponse(BaseModel):
    """WebSocket 临时令牌响应"""

    websocket_token: str = Field(..., description="WebSocket 临时令牌")
    expires_in: int = Field(..., description="过期时间（秒）")


# 消息


class TextContent(BaseModel):
    """消息中的文本内容"""

    type: Literal["text"] = "text"
    text: str = Field(..., description="文本内容")


class ImageContent(BaseModel):
    """消息中的图片内容"""

    type: Literal["image_url"] = "image_url"
    image_url: str = Field(..., description="图片链接")


class ToolCallPart(BaseModel):
    """消息中的工具调用内容"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    args: dict = Field(default_factory=dict, description="工具参数")


class ToolResultPart(BaseModel):
    """消息中的工具结果内容"""

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
    """附件"""

    f_path: str = Field(..., description="工作区内的文件路径")


class MessageSchema(BaseModel):
    """消息"""

    message_id: int | None = Field(default=None, description="消息ID")
    context_seq: int | None = Field(default=None, description="对话内上下文顺序号")
    role: MessageRole = Field(..., description="发送者")
    parts: list[MessagePart] = Field(..., description="消息片段")
    attachments: list[Attachment] | None = Field(default=None, description="附件列表")
    finish_reason: FinishReason | None = Field(default=None, description="完成原因")
    timestamp: datetime | None = Field(default=None, description="发送时间")


class WebSocketChatRequest(BaseModel):
    """WebSocket 聊天请求"""

    message: MessageSchema = Field(..., description="用户消息")


class DeleteAttachmentRequest(BaseModel):
    """删除附件请求"""

    conversation_id: int = Field(..., description="对话ID")
    f_path: str = Field(..., description="工作区内的文件路径")


class MessageListResponse(BaseModel):
    """消息列表响应"""

    messages: list[MessageSchema]


class WebSocketMessageResponse(BaseModel):
    """WebSocket 消息响应"""

    type: Literal["message"] = "message"
    message: MessageSchema = Field(..., description="消息内容")


class WebSocketErrorResponse(BaseModel):
    """WebSocket 错误响应"""

    type: Literal["error"] = "error"
    content: str = Field(..., description="错误信息")


class UploadAttachmentResponse(BaseModel):
    """上传附件响应"""

    attachment: Attachment = Field(..., description="上传后的附件信息")
