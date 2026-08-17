import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from langchain_core.messages import BaseMessage, ToolMessage
from loguru import logger

from app.agent.agent import (
    agent_manager,
    get_agent_config,
)
from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.mappers import message_mapper
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.routes.api.v1.chat import schemas as chat_schema

_SEMANTIC_RECALL_RESULT_TOOLS = {
    "search_semantic_resources",
    "get_semantic_recall",
    "list_semantic_recalls",
    "merge_semantic_recalls",
}


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


async def delete_conversation_data(user_id: int, conversation_id: UUID) -> None:
    """删除会话的 Agent 状态和独立语义召回记录"""
    recall_repo = SemanticRecallPGRepo(langgraph_postgres_manager.get_store())
    await recall_repo.delete_all(user_id, conversation_id)
    await agent_manager.delete_agent(user_id, conversation_id)


async def _execute_agent(
    input_messages: list[BaseMessage],
    user_id: int,
    conversation_id: UUID,
) -> AsyncGenerator[dict[str, Any]]:
    """执行 Agent 并流式返回原始更新"""
    async with agent_manager.execution(user_id, conversation_id):
        agent = await agent_manager.get_agent(user_id, conversation_id)
        config = get_agent_config(user_id, conversation_id)
        async for chunk in agent.astream(
            input={"messages": input_messages},
            config=config,
        ):
            yield chunk


def _semantic_recall_ids(payload: dict[str, Any]) -> list[str]:
    """从语义召回工具结果中提取记录 ID"""
    recall_ids: list[str] = []
    direct_id = payload.get("recall_id")
    if isinstance(direct_id, str):
        recall_ids.append(direct_id)

    recall = payload.get("recall")
    if isinstance(recall, dict) and isinstance(recall.get("recall_id"), str):
        recall_ids.append(recall["recall_id"])

    recalls = payload.get("recalls")
    if isinstance(recalls, list):
        recall_ids.extend(
            item["recall_id"]
            for item in recalls
            if isinstance(item, dict) and isinstance(item.get("recall_id"), str)
        )
    return list(dict.fromkeys(recall_ids))


def compact_semantic_recall_message(message: ToolMessage) -> ToolMessage | None:
    """将完整召回工具历史压缩为可重新读取的记录引用"""
    if message.name not in _SEMANTIC_RECALL_RESULT_TOOLS:
        return None
    if not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") == "error":
        return None

    recall_ids = _semantic_recall_ids(payload)
    if not recall_ids:
        return None
    compact_content = json.dumps(
        {
            "status": "stored",
            "recall_ids": recall_ids,
            "message": "Use get_semantic_recall to load a stored recall",
        },
        ensure_ascii=False,
    )
    if compact_content == message.content:
        return None
    return message.model_copy(update={"content": compact_content})


async def compact_semantic_recall_context(
    user_id: int,
    conversation_id: UUID,
) -> None:
    """在回合结束后从模型历史中移除完整召回载荷"""
    async with agent_manager.execution(user_id, conversation_id):
        agent = await agent_manager.get_agent(user_id, conversation_id)
        config = get_agent_config(user_id, conversation_id)
        state = await agent.aget_state(config)
        messages = state.values.get("messages", [])
        if not isinstance(messages, list):
            return
        replacements = [
            compact
            for message in messages
            if isinstance(message, ToolMessage)
            if (compact := compact_semantic_recall_message(message)) is not None
        ]
        if replacements:
            await agent.aupdate_state(config, {"messages": replacements})


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

    try:
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
    finally:
        try:
            await compact_semantic_recall_context(user_id, conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(f"{conversation_id=}: compact semantic recalls failed")

    logger.info(f"{conversation_id=}: agent finished")
