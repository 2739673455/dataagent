"""聊天接口的请求与响应模型"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)


class CreateConversationRequest(BaseModel):
    """创建对话请求"""

    is_draft: bool = Field(default=False, description="是否创建草稿对话")
    initial_message: str | None = Field(
        default=None,
        max_length=20_000,
        description="用于初始化标题的首条用户文本",
    )


class DeleteConversationRequest(BaseModel):
    """删除对话请求"""

    conversation_ids: list[UUID] = Field(..., description="对话ID列表")


class UpdateConversationRequest(BaseModel):
    """更新对话请求"""

    conversation_id: UUID = Field(..., description="对话ID")
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ] = Field(description="对话标题")


class ConversationResponse(BaseModel):
    """对话响应"""

    conversation_id: UUID
    title: str
    update_at: datetime


class ConversationListResponse(BaseModel):
    """对话列表响应"""

    conversations: list[ConversationResponse]


class TextContent(BaseModel):
    """消息中的文本内容"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(..., description="文本内容")


class ImageContent(BaseModel):
    """消息中的图片内容"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]
    image_url: str = Field(..., description="图片链接")


class ToolCallPart(BaseModel):
    """消息中的工具调用内容"""

    type: Literal["tool_call"]
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    args: dict = Field(default_factory=dict, description="工具参数")


class ToolResultPart(BaseModel):
    """消息中的工具结果内容"""

    type: Literal["tool_result"]
    tool_call_id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    content: str = Field(..., description="工具执行结果")


MessageRole = Literal["user", "assistant", "tool", "system"]
FinishReason = str
UserMessagePart = Annotated[
    TextContent | ImageContent,
    Field(discriminator="type"),
]
MessagePart = Annotated[
    TextContent | ImageContent | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Attachment(BaseModel):
    """附件"""

    f_path: str = Field(..., description="工作区内的文件路径")
    media_type: str | None = Field(default=None, description="附件媒体类型")
    description: str | None = Field(default=None, description="附件说明")


class AttachmentReference(BaseModel):
    """用户消息引用的已上传附件"""

    model_config = ConfigDict(extra="forbid")

    f_path: str = Field(..., description="工作区内的文件路径")


class UserMessageRequest(BaseModel):
    """用户提交给 Agent 的消息"""

    model_config = ConfigDict(extra="forbid")

    parts: list[UserMessagePart] = Field(..., description="文本和图片片段")
    attachments: list[AttachmentReference] | None = Field(
        default=None,
        description="已上传附件引用",
    )

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        """校验消息至少包含一个片段或附件"""
        if not self.parts and not self.attachments:
            raise ValueError("消息内容或附件不能为空")
        return self


class MessageResponse(BaseModel):
    """返回给客户端的消息"""

    message_id: str | None = Field(default=None, description="LangGraph 消息ID")
    role: MessageRole = Field(..., description="发送者")
    parts: list[MessagePart] = Field(..., description="消息片段")
    attachments: list[Attachment] | None = Field(default=None, description="附件列表")
    finish_reason: FinishReason | None = Field(default=None, description="完成原因")


class ChatStreamRequest(BaseModel):
    """SSE 聊天请求"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="对话ID")
    message: UserMessageRequest = Field(..., description="用户消息")


class DeleteAttachmentRequest(BaseModel):
    """删除附件请求"""

    conversation_id: UUID = Field(..., description="对话ID")
    f_path: str = Field(..., description="工作区内的文件路径")


class MessageListResponse(BaseModel):
    """消息列表响应"""

    messages: list[MessageResponse]


class ChatStreamMessageEvent(BaseModel):
    """SSE 消息事件"""

    type: Literal["message"]
    message: MessageResponse = Field(..., description="消息内容")


class ChatStreamErrorEvent(BaseModel):
    """SSE 错误事件"""

    type: Literal["error"]
    content: str = Field(..., description="错误信息")


class ChatStreamDoneEvent(BaseModel):
    """SSE 完成事件"""

    type: Literal["done"]


ChatStreamEventPayload = Annotated[
    ChatStreamMessageEvent | ChatStreamErrorEvent | ChatStreamDoneEvent,
    Field(discriminator="type"),
]


class ChatStreamEvent(RootModel[ChatStreamEventPayload]):
    """单个 SSE data 帧的 JSON 事件"""


class UploadAttachmentResponse(BaseModel):
    """上传附件响应"""

    attachment: Attachment = Field(..., description="上传后的附件信息")
