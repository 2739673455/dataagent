"""会话模型"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConversationInfo(BaseModel):
    """会话目录信息"""

    id: UUID
    user_id: int
    title: str
    title_pending: bool = False
    is_draft: bool
    create_at: datetime
    update_at: datetime
