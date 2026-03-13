from collections.abc import AsyncIterator

from app.mappers import message_mapper
from app.repositories import conversation_repo, message_repo
from app.schemas import chat_schema
from app.services import agent_service
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


async def stream_chat(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
) -> AsyncIterator[chat_schema.MessageSchema]:
    """基于当前历史消息处理一轮聊天并流式返回响应"""
    logger.info(f"{conversation_id=}: {user_message=}")

    # 添加用户消息
    messages.append(
        message_mapper.schema_to_langchain_message(
            user_message, user_id=user_id, conversation_id=conversation_id
        )
    )

    # 存储到数据库
    user_message_entity = message_mapper.schema_to_entity(user_message, conversation_id)
    await message_repo.create(db_session, user_message_entity)
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)

    # 调用 Agent
    async for chunk in agent_service.astream(user_id, conversation_id, messages):
        logger.info(f"{conversation_id=}: agent_response={chunk}")
        responses = message_mapper.agent_chunk_to_schemas(chunk)
        if not responses:
            continue

        for response in responses:
            # 添加 Agent 响应
            messages.append(
                message_mapper.schema_to_langchain_message(
                    response, user_id=user_id, conversation_id=conversation_id
                )
            )

            # 存储到数据库
            message_entity = message_mapper.schema_to_entity(response, conversation_id)
            await message_repo.create(db_session, message_entity)
            # 刷新对话更新时间
            await conversation_repo.touch_update_at(db_session, conversation_id)

            # 返回 Agent 响应
            yield response
