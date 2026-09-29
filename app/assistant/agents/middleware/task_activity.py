"""将内置 task 的执行过程转成当前聊天的实时活动。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.assistant.events.stream import MessageDeltaParser, update_messages
from app.assistant.services.types import (
    SubagentActivityWriter,
    SubagentMessageActivity,
    SubagentMessageDeltaActivity,
    SubagentStatusActivity,
    SubagentThinkingDeltaActivity,
)
from app.shared.contracts.analysis import AGENT_TYPES, AgentType

_task_activity: ContextVar[tuple[str, AgentType, SubagentActivityWriter]] = ContextVar(
    "task_activity"
)


class TaskActivityMiddleware(AgentMiddleware):
    """只转发任务状态；任务选择、执行和结果处理由内置 task 完成。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call["name"] != "task":
            return await handler(request)
        call_id = request.tool_call["id"]
        if call_id is None:
            return await handler(request)
        agent_type = request.tool_call["args"].get("subagent_type")
        if agent_type not in AGENT_TYPES:
            return await handler(request)
        writer = request.runtime.stream_writer
        token = _task_activity.set((call_id, agent_type, writer))
        writer(SubagentStatusActivity(call_id, agent_type, "running"))
        try:
            result = await handler(request)
        except asyncio.CancelledError:
            writer(SubagentStatusActivity(call_id, agent_type, "cancelled"))
            raise
        except Exception:
            writer(SubagentStatusActivity(call_id, agent_type, "failed"))
            raise
        else:
            failed = isinstance(result, ToolMessage) and result.status == "error"
            writer(
                SubagentStatusActivity(
                    call_id, agent_type, "failed" if failed else "completed"
                )
            )
            return result
        finally:
            _task_activity.reset(token)


async def stream_task(
    graph: CompiledStateGraph, state: dict, config: RunnableConfig
) -> dict:
    """转发临时子图的消息，返回最终状态供 task 提取结果。"""
    call_id, agent_type, writer = _task_activity.get()
    deltas = MessageDeltaParser()
    output = None
    async for part in graph.astream(
        state, config, stream_mode=["updates", "values", "messages"], version="v2"
    ):
        data = part["data"]
        if part["type"] == "values":
            output = data
        elif part["type"] == "messages":
            for kind, delta in deltas.parse(data):
                activity = (
                    SubagentThinkingDeltaActivity
                    if kind == "thinking"
                    else SubagentMessageDeltaActivity
                )
                writer(activity(call_id, agent_type, **delta))
        elif part["type"] == "updates":
            for message in update_messages(data):
                writer(SubagentMessageActivity(call_id, agent_type, message))
    if not isinstance(output, Mapping):
        raise TypeError("子 Agent 未产生最终状态")
    return dict(output)
