"""发布内置 task 的开始、完成、失败和取消状态。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.assistant.services.types import (
    SubagentStatusActivity,
)
from app.shared.contracts.analysis import AGENT_TYPES


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
