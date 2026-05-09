"""对话数据访问"""

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat import Conversation


async def create(
    db_session: AsyncSession,
    user_id: int,
    title: str,
    is_draft: int = 0,
) -> Conversation:
    """创建新对话

    Args:
        db_session: 数据库会话
        user_id: 用户 ID
        title: 对话标题
        is_draft: 是否为草稿对话（1-是，0-否）

    Returns:
        创建成功的对话对象
    """
    conversation = Conversation(user_id=user_id, title=title, is_draft=is_draft)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    return conversation


async def update(
    db_session: AsyncSession,
    conversation: Conversation,
    title: str | None = None,
    is_draft: int | None = None,
    yn: int | None = None,
) -> None:
    """更新对话信息

    只更新传入的非 None 字段

    Args:
        db_session: 数据库会话
        conversation: 要更新的对话对象
        title: 新标题，为 None 则不更新
        is_draft: 是否为草稿对话（1-是，0-否），为 None 则不更新
        yn: 启用状态（1-启用，0-禁用），为 None 则不更新
    """
    if title is not None:
        conversation.title = title
    if is_draft is not None:
        conversation.is_draft = is_draft
    if yn is not None:
        conversation.yn = yn
    await db_session.commit()


async def touch_update_at(
    db_session: AsyncSession,
    conversation_id: int,
) -> None:
    """手动刷新对话更新时间"""
    stmt = (
        sql_update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(update_at=func.now())
    )
    await db_session.execute(stmt)
    await db_session.commit()


async def get_by_id(
    db_session: AsyncSession,
    conversation_id: int,
    is_draft: int | None = None,
    yn: int | None = 1,
) -> Conversation | None:
    """通过 ID 获取对话

    Args:
        db_session: 数据库会话
        conversation_id: 对话 ID
        is_draft: 草稿状态过滤，为 None 则不过滤
        yn: 启用状态过滤，为 None 则不过滤

    Returns:
        对话对象，不存在则返回 None
    """
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    if is_draft is not None:
        stmt = stmt.where(Conversation.is_draft == is_draft)
    if yn is not None:
        stmt = stmt.where(Conversation.yn == yn)
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


async def ls(
    db_session: AsyncSession,
    user_id: int,
    is_draft: int | None = 0,
    yn: int | None = 1,
) -> list[Conversation]:
    """获取某个用户所有对话

    Args:
        db_session: 数据库会话
        user_id: 用户 ID
        is_draft: 草稿状态过滤，为 None 则不过滤
        yn: 启用状态过滤，为 None 则不过滤

    Returns:
        对话列表
    """
    base_stmt = select(Conversation).where(Conversation.user_id == user_id)
    if is_draft is not None:
        base_stmt = base_stmt.where(Conversation.is_draft == is_draft)
    if yn is not None:
        base_stmt = base_stmt.where(Conversation.yn == yn)
    stmt = base_stmt.order_by(Conversation.update_at.desc(), Conversation.id.desc())
    result = await db_session.execute(stmt)
    return list(result.scalars().all())
