"""聊天消息持久化元数据"""

from datetime import UTC, datetime

from langchain_core.messages import BaseMessage

MESSAGE_CREATED_AT_KEY = "dataagent_created_at"
MESSAGE_PAYLOAD_KEY = "dataagent_message"


def stamp_message_created_at(
    message: BaseMessage,
    created_at: datetime | None = None,
) -> None:
    """为待持久化消息写入创建时间"""
    message.additional_kwargs.setdefault(
        MESSAGE_CREATED_AT_KEY,
        (created_at or datetime.now(UTC)).isoformat(),
    )


def get_message_created_at(message: BaseMessage) -> datetime | None:
    """读取消息创建时间"""
    value = message.additional_kwargs.get(MESSAGE_CREATED_AT_KEY)
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
