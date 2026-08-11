import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from langchain_core.messages import BaseMessage
from loguru import logger

from app.agent.agent import (
    agent_manager,
    get_agent_config,
)
from app.mappers import message_mapper
from app.routes.api.v1.chat import schemas as chat_schema


async def list_messages(
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageSchema]:
    """从 LangGraph 最新线程状态读取消息"""
    agent = await agent_manager.get_agent(user_id, conversation_id)
    state = await agent.aget_state(get_agent_config(user_id, conversation_id))
    messages = state.values.get("messages", [])
    if not isinstance(messages, list):
        return []

    result: list[chat_schema.MessageSchema] = []
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        if schema := message_mapper.langchain_message_to_schema(message):
            result.append(schema)
    return result


async def delete_conversation_state(user_id: int, conversation_id: UUID) -> None:
    """删除会话的 LangGraph 持久化状态"""
    await agent_manager.delete_agent(user_id, conversation_id)


async def _execute_agent(
    input_messages: list[BaseMessage],
    user_id: int,
    conversation_id: UUID,
) -> AsyncGenerator[dict[str, Any]]:
    """执行 Agent 并流式返回原始更新"""
    agent = await agent_manager.get_agent(user_id, conversation_id)
    config = get_agent_config(user_id, conversation_id)
    async for chunk in agent.astream(
        input={"messages": input_messages},
        config=config,
    ):
        yield chunk


async def run_agent_turn(
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.MessageSchema,
    cancel: asyncio.Event,
) -> AsyncGenerator[chat_schema.MessageSchema]:
    """执行一轮 Agent 对话并流式返回响应"""
    logger.info(f"{conversation_id=}: {user_message=}")

    input_messages: list[BaseMessage] = [
        await message_mapper.schema_to_human_message(
            user_message,
            user_id,
            conversation_id,
        )
    ]

    while True:
        last_finish_reason: str | None = None

        async for chunk in _execute_agent(
            input_messages,
            user_id,
            conversation_id,
        ):
            if cancel.is_set():
                logger.info(f"{conversation_id=}: agent cancelled")
                break

            logger.info(f"{conversation_id=}: agent_response={chunk}")
            for response in message_mapper.agent_chunk_to_schemas(chunk):
                last_finish_reason = response.finish_reason
                yield response

        if cancel.is_set() or last_finish_reason in {None, "stop"}:
            break

        # 空增量会保留 Checkpointer 中的已有状态并继续生成
        input_messages = []

    logger.info(f"{conversation_id=}: agent finished")
