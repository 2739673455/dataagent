"""消息数据访问"""

from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat import Message


async def create(db_session: AsyncSession, message: Message) -> Message:
    """创建新消息

    Args:
        db_session: 数据库会话
        message: 要创建的消息对象

    Returns:
        创建成功的消息对象
    """
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    return message


async def update_yn_by_ids(
    db_session: AsyncSession,
    message_ids: list[int],
    yn: int,
) -> None:
    """批量更新消息启用状态

    Args:
        db_session: 数据库会话
        message_ids: 要更新的消息 ID 列表
        yn: 启用状态（1-启用，0-禁用）
    """
    stmt = sql_update(Message).where(Message.id.in_(message_ids)).values(yn=yn)
    await db_session.execute(stmt)
    await db_session.commit()


async def update_yn_by_conversation_id(
    db_session: AsyncSession,
    conversation_id: int,
    yn: int,
) -> None:
    """按对话 ID 批量更新消息启用状态

    Args:
        db_session: 数据库会话
        conversation_id: 对话 ID
        yn: 启用状态（1-启用，0-禁用）
    """
    stmt = sql_update(Message).where(Message.conversation_id == conversation_id).values(yn=yn)
    await db_session.execute(stmt)
    await db_session.commit()


async def ls(
    db_session: AsyncSession,
    conversation_id: int,
    yn: int | None = 1,
) -> list[Message]:
    """获取指定对话的消息列表

    Args:
        db_session: 数据库会话
        conversation_id: 对话 ID
        yn: 启用状态过滤，为 None 则不过滤

    Returns:
        按上下文顺序正序排列的消息列表
    """
    base_stmt = select(Message).where(Message.conversation_id == conversation_id)
    if yn is not None:
        base_stmt = base_stmt.where(Message.yn == yn)
    stmt = base_stmt.order_by(Message.context_seq.asc(), Message.id.asc())
    result = await db_session.execute(stmt)
    return list(result.scalars().all())
