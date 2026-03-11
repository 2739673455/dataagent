from collections.abc import AsyncIterator

from app.mappers import message_mapper
from app.repositories import conversation_repo, message_repo
from app.schemas import chat_schema
from app.services import agent_service
from sqlalchemy.ext.asyncio import AsyncSession


async def load_langchain_messages(
    db_session: AsyncSession,
    conversation_id: int,
) -> list[dict]:
    """加载会话历史并转换为 LangChain 消息格式"""
    messages = await message_repo.ls(db_session, conversation_id)
    return [
        message_mapper.schema_to_langchain_message(message_mapper.entity_to_schema(i))
        for i in messages
    ]


async def stream_chat(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
) -> AsyncIterator[chat_schema.WebSocketMessageResponse]:
    """基于当前历史消息处理一轮聊天并流式返回响应"""
    # 添加用户消息
    messages.append(message_mapper.schema_to_langchain_message(user_message))

    # 存储到数据库
    user_message_entity = message_mapper.schema_to_entity(user_message, conversation_id)
    await message_repo.create(
        db_session=db_session,
        conversation_id=user_message_entity.conversation_id,
        role=user_message_entity.role,
        parts=user_message_entity.parts,
        attachments=user_message_entity.attachments,
    )
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)

    # 调用 Agent
    async for chunk in agent_service.astream(user_id, conversation_id, messages):
        response = message_mapper.agent_chunk_to_schema(chunk)
        if response is None:
            continue

        # 添加 Agent 响应
        messages.append(message_mapper.schema_to_langchain_message(response))

        # 存储到数据库
        message_entity = message_mapper.schema_to_entity(response, conversation_id)
        await message_repo.create(
            db_session=db_session,
            conversation_id=message_entity.conversation_id,
            role=message_entity.role,
            parts=message_entity.parts,
            attachments=message_entity.attachments,
        )
        # 刷新对话更新时间
        await conversation_repo.touch_update_at(db_session, conversation_id)

        # 返回 Agent 响应
        yield chat_schema.WebSocketMessageResponse(message=response)
