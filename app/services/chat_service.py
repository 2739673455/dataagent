import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from langchain_core.messages import BaseMessage
from loguru import logger

from app.agents.contracts import PlannerTurnContext, build_planner_config
from app.agents.manager import AnalysisAgentBundle, agent_manager
from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.mappers import message_mapper
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.routes.api.v1.chat import schemas as chat_schema


async def list_messages(
    user_id: int,
    conversation_id: UUID,
) -> list[chat_schema.MessageSchema]:
    """从 LangGraph 最新线程状态读取消息"""
    bundle = await agent_manager.get_agent_bundle(user_id, conversation_id)
    state = await bundle.planner.aget_state(
        build_planner_config(user_id, conversation_id)
    )
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
    bundle: AnalysisAgentBundle,
    turn_context: PlannerTurnContext,
) -> AsyncGenerator[dict[str, Any]]:
    """执行 Agent 并流式返回原始更新"""
    config = build_planner_config(
        turn_context.user_id,
        turn_context.conversation_id,
    )
    config.setdefault("configurable", {})["planner_run_id"] = (
        turn_context.planner_run_id
    )
    async for chunk in bundle.planner.astream(
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
    logger.info(
        f"agent turn started: user_id={user_id}, conversation_id={conversation_id}, "
        f"message_id={user_message.message_id}, parts={len(user_message.parts)}, "
        f"attachments={len(user_message.attachments or ())}"
    )

    input_messages: list[BaseMessage] = [
        await message_mapper.schema_to_human_message(
            user_message,
            user_id,
            conversation_id,
        )
    ]

    bundle = await agent_manager.get_agent_bundle(user_id, conversation_id)
    async with agent_manager.execution(
        user_id,
        conversation_id,
        bundle=bundle,
    ) as turn_context:
        continuation_count = 0
        while True:
            last_finish_reason: str | None = None

            async for chunk in _execute_agent(
                input_messages,
                bundle,
                turn_context,
            ):
                if cancel.is_set():
                    logger.info(f"{conversation_id=}: agent cancelled")
                    break

                responses = message_mapper.agent_chunk_to_schemas(chunk)
                logger.debug(
                    f"agent stream update: user_id={user_id}, "
                    f"conversation_id={conversation_id}, nodes={tuple(chunk)}, "
                    f"messages={len(responses)}"
                )
                for response in responses:
                    last_finish_reason = response.finish_reason
                    yield response

            if (
                cancel.is_set()
                or last_finish_reason is None
                or last_finish_reason == "stop"
            ):
                break
            if continuation_count >= turn_context.max_continuations:
                raise PlannerContinuationLimitError(
                    turn_context.max_continuations,
                    last_finish_reason,
                )

            continuation_count += 1
            # 空增量会保留 Checkpointer 中的已有状态并继续生成
            input_messages = []

    logger.info(
        f"agent turn finished: user_id={user_id}, conversation_id={conversation_id}"
    )


class PlannerContinuationLimitError(RuntimeError):
    """Planner 自动续写次数超过服务端硬限制"""

    def __init__(self, max_continuations: int, finish_reason: str) -> None:
        self.max_continuations = max_continuations
        self.finish_reason = finish_reason
        super().__init__(
            f"Planner exceeded {max_continuations} continuations "
            f"after finish reason {finish_reason!r}"
        )
