"""会话创建、更新、删除与目录查询协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)


class CreateConversationRequest(BaseModel):
    """创建对话请求。"""

    model_config = ConfigDict(extra="forbid")

    is_draft: bool = Field(default=False, description="是否创建草稿对话")
    initial_message: str | None = Field(
        default=None,
        description="用于初始化标题的首条用户文本",
    )


class DeleteConversationRequest(BaseModel):
    """删除对话请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_ids: list[UUID] = Field(
        ...,
        min_length=1,
        description="对话ID列表",
    )


class UpdateConversationRequest(BaseModel):
    """更新对话请求。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="对话ID")
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ] = Field(description="对话标题")


class ConversationResponse(BaseModel):
    """对话响应。"""

    conversation_id: UUID
    title: str
    update_at: datetime
    running: bool


class ConversationListResponse(BaseModel):
    """对话列表响应。"""

    conversations: list[ConversationResponse]
