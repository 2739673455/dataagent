from datetime import datetime

from pydantic import BaseModel, Field


class TextContent(BaseModel):
    type: str = "text"
    text: str = Field(..., description="文本内容")


class ImageContent(BaseModel):
    type: str = "image_url"
    image_url: str = Field(..., description="图片链接")


class Attachments(BaseModel):
    name: str = Field(..., description="附件名称")
    url: str = Field(..., description="附件链接")


class MessageItem(BaseModel):
    message_id: int | None = Field(default=None, description="消息ID")
    role: str = Field(..., description="发送者 (user/assistant)")
    content: str | list[TextContent | ImageContent] = Field(..., description="消息内容")
    attachments: list[Attachments] | None = Field(default=None, description="附件列表")
    timestamp: datetime | None = Field(default=None, description="发送时间")


class GetUploadPresignedUrlRequest(BaseModel):
    conversation_id: int = Field(..., description="对话ID")
    suffixes: list[str] = Field(..., description="文件后缀列表")


class SendMessageRequest(BaseModel):
    conversation_id: int = Field(..., description="对话ID")
    messages: list[MessageItem] = Field(..., description="消息列表")
    base_url: str = Field(..., description="OpenAI 兼容 API URL")
    model_name: str | None = Field(default=None, description="模型名称")
    api_key: str | None = Field(default=None, description="API 密钥")
    params: dict | None = Field(default=None, description="配置参数")


class WebSocketChatRequest(BaseModel):
    type: str = Field(..., description="消息类型 (chat)")
    messages: list[MessageItem] = Field(..., description="消息列表")
    base_url: str = Field(..., description="OpenAI 兼容 API URL")
    model_name: str | None = Field(default=None, description="模型名称")
    api_key: str | None = Field(default=None, description="API 密钥")
    params: dict | None = Field(default=None, description="配置参数")


class GetUploadPresignedUrlResponse(BaseModel):
    urls: list[str]


class MessageListResponse(BaseModel):
    messages: list[MessageItem]


class ConversationTitleResponse(BaseModel):
    title: str
