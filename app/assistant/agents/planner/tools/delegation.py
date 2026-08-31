"""专业 Agent 委派工具"""

from dataclasses import replace
from typing import Annotated, cast

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import ValidationError

from app.assistant.agents.contracts import (
    DelegationRequest,
    SubagentActivity,
    SubagentActivityWriter,
)
from app.assistant.agents.session_service import AgentSessionService
from app.shared.contracts.analysis import AgentType

_PTC_DELEGATION_ID_PREFIX = "ptc_delegation_"


def _parent_eval_tool_call_id(runtime: ToolRuntime) -> str | None:
    """从 QuickJS PTC 的派生运行时中定位父 eval 工具调用"""
    tool_call_id = runtime.tool_call_id
    if not tool_call_id or not tool_call_id.startswith(_PTC_DELEGATION_ID_PREFIX):
        return None
    messages = runtime.state.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in reversed(message.tool_calls):
            if tool_call.get("name") == "eval" and tool_call.get("id"):
                return str(tool_call["id"])
    return None


def create_delegation_tool(service: AgentSessionService) -> BaseTool:
    """创建只绑定当前用户会话的 delegation Tool"""

    @tool("delegation")
    async def delegation(
        runtime: ToolRuntime,
        analysis_id: Annotated[
            str,
            "分析标识，只能包含小写字母、数字、连字符和下划线，最长 64 字符",
        ],
        agent_type: Annotated[
            AgentType,
            "专业 Agent 类型",
        ],
        session_id: Annotated[
            str,
            "专业 Session 标识，首次创建后续接和修补时必须复用",
        ],
        message: Annotated[
            str,
            "交给专业 Agent 的完整目标、输入产物路径和约束",
        ],
    ) -> dict[str, object]:
        """创建或恢复专业 Agent Session 并返回可验证的结构化结果"""
        try:
            request = DelegationRequest(
                analysis_id=analysis_id,
                agent_type=agent_type,
                session_id=session_id,
                message=message,
            )
        except ValidationError as exc:
            return {
                "status": "error",
                "code": "invalid_delegation_request",
                "message": "委派请求无效",
                "details": exc.errors(include_url=False),
            }
        parent_tool_call_id = _parent_eval_tool_call_id(runtime)
        delegation_id = runtime.tool_call_id
        if delegation_id is None:
            raise RuntimeError("delegation 工具缺少 tool_call_id")
        activity_writer: SubagentActivityWriter = runtime.stream_writer
        if parent_tool_call_id is not None:
            service.begin_eval_delegation(
                parent_tool_call_id,
                delegation_id,
                request,
            )

            def write_eval_activity(activity: SubagentActivity) -> None:
                activity_writer(
                    replace(
                        activity,
                        parent_tool_call_id=parent_tool_call_id,
                        instruction=request.message,
                    )
                )

            delegated_activity_writer = write_eval_activity
        else:
            delegated_activity_writer = activity_writer
        try:
            result = await service.execute_delegation(
                request,
                cast(RunnableConfig, runtime.config),
                delegation_id=delegation_id,
                activity_writer=delegated_activity_writer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("执行专业 Agent 委派失败")
            return {
                "status": "error",
                "code": "delegation_failed",
                "message": "专业 Agent 委派失败",
                "details": [
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            }
        if parent_tool_call_id is not None:
            service.finish_eval_delegation(
                parent_tool_call_id,
                delegation_id,
                result,
            )
        return result.model_dump(mode="json")

    return delegation
