"""持久化 eval 内部发起的专业 Agent 委派"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.analytics.agents.contracts import EVAL_DELEGATIONS_KEY
from app.analytics.agents.session_service import AgentSessionService


class EvalDelegationMiddleware(AgentMiddleware[Any, Any, Any]):
    """把 QuickJS PTC 委派清单附加到父 eval 的 ToolMessage"""

    def __init__(self, session_service: AgentSessionService) -> None:
        """绑定当前 Conversation 的专业 Session 服务"""
        self._session_service = session_service

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command[Any]],
        ],
    ) -> ToolMessage | Command[Any]:
        """执行 eval，并将其内部委派记录写入可持久化消息元数据"""
        if request.tool_call.get("name") != "eval":
            return await handler(request)

        tool_call_id = str(request.tool_call.get("id") or "")
        try:
            result = await handler(request)
        except BaseException:
            self._session_service.take_eval_delegations(tool_call_id)
            raise

        records = self._session_service.take_eval_delegations(tool_call_id)
        if not records or not isinstance(result, ToolMessage):
            return result
        additional_kwargs = {
            **result.additional_kwargs,
            EVAL_DELEGATIONS_KEY: [
                record.model_dump(mode="json") for record in records
            ],
        }
        return result.model_copy(update={"additional_kwargs": additional_kwargs})
