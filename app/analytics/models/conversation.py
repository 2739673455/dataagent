"""会话目录模型"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConversationInfo(BaseModel):
    """会话目录信息"""

    id: UUID
    user_id: int
    title: str
    title_pending: bool = False
    title_source: str | None = None
    title_generation_requested_at: datetime | None = None
    is_draft: bool
    deletion_requested_at: datetime | None = None
    create_at: datetime
    update_at: datetime
