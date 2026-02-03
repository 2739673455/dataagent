from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat import Conversation, Message
from app.exceptions.conversation import ConversationNotFoundError


async def get_conversations(
    db_session: AsyncSession, user_id: int
) -> Sequence[Conversation]:
    """获取对话列表"""
    stmt = select(Conversation).where(Conversation.user_id == user_id)
    result = await db_session.execute(stmt)
    return result.scalars().all()


async def create_conversation(
    db_session: AsyncSession, user_id: int, model_config_id: int
) -> Conversation:
    """创建对话"""
    try:
        conversation = Conversation(user_id=user_id, model_config_id=model_config_id)
        db_session.add(conversation)
        await db_session.commit()
        await db_session.refresh(conversation)
        return conversation
    except Exception:
        await db_session.rollback()
        raise


async def update_conversation_data(
    db_session: AsyncSession, conversation_id: int, conversation_data: dict
) -> None:
    """更新对话标题或模型配置"""
    try:
        stmt = select(Conversation).where(Conversation.id == conversation_id)
        result = await db_session.execute(stmt)
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise ConversationNotFoundError  # 对话不存在
        conversation.title = conversation_data.get("title", conversation.title)
        conversation.model_config_id = conversation_data.get(
            "model_config_id", conversation.model_config_id
        )
        await db_session.commit()
        await db_session.refresh(conversation)
    except Exception:
        await db_session.rollback()
        raise


async def delete_conversations(db_session: AsyncSession, ids: list[int]) -> None:
    """批量删除对话"""
    try:
        stmt = select(Conversation).where(Conversation.id.in_(ids))
        result = await db_session.execute(stmt)
        conversations = result.scalars().all()
        if not conversations:
            raise ConversationNotFoundError  # 对话不存在
        # 删除关联的消息
        await db_session.execute(
            delete(Message).where(Message.conversation_id.in_(ids))
        )
        # 删除对话
        for conversation in conversations:
            await db_session.delete(conversation)
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        raise
