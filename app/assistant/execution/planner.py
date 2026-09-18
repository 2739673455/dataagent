"""Planner 回合执行、续写与恢复，及执行事件到聊天协议的转换。"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from langchain_core.messages import BaseMessage
from langgraph.types import StreamPart
from loguru import logger

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.events import schemas as chat_schema
from app.assistant.events.projection import (
    langchain_message_to_schema_with_artifacts,
    normalize_finish_reason,
    schema_to_human_message,
    subagent_activity_to_event,
)
from app.assistant.events.stream import MessageDeltaParser
from app.assistant.execution.contracts import (
    AgentRuntimeManager,
    ConversationFileInspector,
)
from app.assistant.execution.types import (
    ConversationAgentRuntime,
    PlannerTurnContext,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
    build_planner_config,
)


async def _execute_agent(
    input_messages: list[BaseMessage] | None,
    runtime: ConversationAgentRuntime,
    turn_context: PlannerTurnContext,
) -> AsyncGenerator[StreamPart[Any, Any]]:
    """执行 Agent 并流式返回原始更新。"""
    config = build_planner_config(
        turn_context.user_id,
        turn_context.conversation_id,
    )
    async for chunk in runtime.planner.astream(
        input={"messages": input_messages} if input_messages is not None else None,
        config=config,
        stream_mode=["updates", "custom", "messages"],
        version="v2",
    ):
        yield chunk


async def _run_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
    input_messages: list[BaseMessage] | None,
    *,
    recall: SemanticRecallRuntime,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """执行新回合或从待执行 Checkpoint 恢复同一回合。"""
    runtime = await agents.get_conversation_runtime(user_id, conversation_id)
    async with agents.execution(
        user_id,
        conversation_id,
        runtime=runtime,
    ) as turn_context:
        continuation_count = 0
        deltas = MessageDeltaParser()
        while True:
            last_finish_reason: str | None = None

            async for chunk in _execute_agent(
                input_messages,
                runtime,
                turn_context,
            ):
                if chunk.get("type") == "custom":
                    activity = chunk.get("data")
                    if isinstance(
                        activity,
                        (
                            SubagentMessageActivity,
                            SubagentMessageDeltaActivity,
                            SubagentThinkingDeltaActivity,
                            SubagentStatusActivity,
                        ),
                    ):
                        event = await subagent_activity_to_event(
                            activity,
                            user_id,
                            conversation_id,
                            recall=recall,
                        )
                        if event is not None:
                            yield event
                    continue
                if chunk.get("type") == "messages":
                    for kind, delta in deltas.parse(chunk.get("data")):
                        if kind == "thinking":
                            yield chat_schema.ChatStreamThinkingEvent(
                                type="thinking", **delta
                            )
                        else:
                            yield chat_schema.ChatStreamMessageDeltaEvent(
                                type="message_delta", **delta
                            )
                    continue
                if chunk.get("type") != "updates":
                    continue
                data = chunk.get("data")
                if not isinstance(data, dict):
                    continue

                responses: list[chat_schema.MessageResponse] = []
                for node in ("model", "tools"):
                    update = data.get(node)
                    messages = (
                        update.get("messages") if isinstance(update, dict) else None
                    )
                    if not isinstance(messages, list):
                        continue
                    for message in messages:
                        response = await langchain_message_to_schema_with_artifacts(
                            message,
                            files,
                            user_id,
                            conversation_id,
                        )
                        if response is not None:
                            responses.append(response)
                logger.debug(
                    f"智能体流式更新: conversation_id={conversation_id}, "
                    f"nodes={tuple(chunk)}, "
                    f"messages={len(responses)}"
                )
                for response in responses:
                    last_finish_reason = normalize_finish_reason(response.finish_reason)
                    yield chat_schema.ChatStreamMessageEvent(
                        type="message",
                        message=response,
                    )

            if last_finish_reason is None or last_finish_reason == "stop":
                break
            if continuation_count >= turn_context.max_continuations:
                raise PlannerContinuationLimitError(
                    turn_context.max_continuations,
                    last_finish_reason,
                )

            continuation_count += 1
            # 空增量会保留 Checkpointer 中的已有状态并继续生成。
            input_messages = []


async def run_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
    user_message: chat_schema.UserMessageRequest,
    *,
    recall: SemanticRecallRuntime,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """执行一轮 Agent 对话并流式返回响应。"""
    logger.info(
        f"智能体回合开始: conversation_id={conversation_id}, "
        f"parts={len(user_message.parts)}, "
        f"attachments={len(user_message.attachments or ())}"
    )
    async for event in _run_agent_turn(
        agents,
        files,
        user_id,
        conversation_id,
        [schema_to_human_message(user_message)],
        recall=recall,
    ):
        yield event

    logger.info(f"智能体回合结束: conversation_id={conversation_id}")


async def resume_agent_turn(
    agents: AgentRuntimeManager,
    files: ConversationFileInspector,
    user_id: int,
    conversation_id: UUID,
    *,
    recall: SemanticRecallRuntime,
) -> AsyncGenerator[chat_schema.ChatStreamEventPayload]:
    """从 Planner 最新 Checkpoint 的待执行任务继续生成。"""
    logger.info(f"智能体回合恢复: conversation_id={conversation_id}")
    async for event in _run_agent_turn(
        agents,
        files,
        user_id,
        conversation_id,
        None,
        recall=recall,
    ):
        yield event
    logger.info(f"智能体回合恢复结束: conversation_id={conversation_id}")


async def can_resume_agent_turn(
    agents: AgentRuntimeManager,
    user_id: int,
    conversation_id: UUID,
) -> bool:
    """检查 Planner 最新 Checkpoint 是否保留待执行任务。"""
    state = await agents.read_planner_state(user_id, conversation_id)
    return bool(state.next_nodes)


class PlannerContinuationLimitError(RuntimeError):
    """Planner 自动续写次数超过服务端硬限制。"""

    def __init__(self, max_continuations: int, finish_reason: str) -> None:
        """初始化包含续写上限和结束原因的异常。"""
        self.max_continuations = max_continuations
        self.finish_reason = finish_reason
        super().__init__(
            f"规划器在结束原因 {finish_reason!r} 下连续续写次数超过上限 ({max_continuations} 次)"
        )


class PlannerTurnNotResumableError(RuntimeError):
    """Planner 最新 Checkpoint 没有待执行节点。"""
