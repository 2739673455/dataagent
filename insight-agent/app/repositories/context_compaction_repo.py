"""上下文压缩数据访问"""

from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat import ContextCompaction


async def create(
    db_session: AsyncSession,
    compaction: ContextCompaction,
) -> ContextCompaction:
    """创建上下文压缩记录"""
    db_session.add(compaction)
    await db_session.commit()
    await db_session.refresh(compaction)
    return compaction


async def update_yn_by_conversation_id(
    db_session: AsyncSession,
    conversation_id: int,
    yn: int,
) -> None:
    """按对话 ID 批量更新上下文压缩记录启用状态"""
    stmt = (
        sql_update(ContextCompaction)
        .where(ContextCompaction.conversation_id == conversation_id)
        .values(yn=yn)
    )
    await db_session.execute(stmt)
    await db_session.commit()


async def get_latest_by_conversation_id(
    db_session: AsyncSession,
    conversation_id: int,
    yn: int | None = 1,
) -> ContextCompaction | None:
    """获取某个对话最新一条上下文压缩记录"""
    stmt = select(ContextCompaction).where(ContextCompaction.conversation_id == conversation_id)
    if yn is not None:
        stmt = stmt.where(ContextCompaction.yn == yn)
    stmt = stmt.order_by(ContextCompaction.end_seq.desc(), ContextCompaction.id.desc())
    result = await db_session.execute(stmt)
    return result.scalars().first()
