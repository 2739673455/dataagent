from collections.abc import AsyncIterator

import openai
from app.mappers import message_mapper
from app.repositories import conversation_repo, message_repo
from app.schemas import chat_schema
from app.services import agent_service
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


async def _add_message(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    message: chat_schema.MessageSchema,
):
    """将消息写入会话上下文与数据库，并同步刷新对话更新时间"""
    # 将消息添加到消息列表
    messages.append(
        message_mapper.schema_to_langchain_message(
            message, user_id=user_id, conversation_id=conversation_id
        )
    )
    # 存储到数据库
    message_entity = message_mapper.schema_to_entity(message, conversation_id)
    await message_repo.create(db_session, message_entity)
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)


async def stream_chat(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
) -> AsyncIterator[chat_schema.MessageSchema]:
    """基于当前历史消息处理一轮聊天并流式返回响应"""
    logger.info(f"{conversation_id=}: {user_message=}")

    await _add_message(db_session, user_id, conversation_id, messages, user_message)

    # 调用 Agent
    try:
        async for chunk in agent_service.astream(user_id, conversation_id, messages):
            logger.info(f"{conversation_id=}: agent_response={chunk}")
            responses = message_mapper.agent_chunk_to_schemas(chunk)
            if not responses:
                continue

            for response in responses:
                await _add_message(
                    db_session, user_id, conversation_id, messages, response
                )
                # 返回 Agent 响应
                yield response

    except openai.NotFoundError as e:
        if "No endpoints found that support image input" not in e.message:
            raise

        # 处理模型不支持图片输入的情况
        text = "当前模型不支持图片输入。"
        response = chat_schema.MessageSchema(
            role="assistant",
            parts=[chat_schema.TextContent(text=text)],
            finish_reason="stop",
        )
        await _add_message(db_session, user_id, conversation_id, messages, response)
        # 返回 Agent 响应
        yield response
