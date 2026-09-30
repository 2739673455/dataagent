"""Planner 的专业 Agent 委派与 Session 管理工具。"""

from typing import Annotated, cast

from langchain.tools import ToolRuntime, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import ConfigDict

from app.assistant.agents.tools.errors import tool_error
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import (
    DelegationRequest,
    DeleteSessionRequest,
    ListSessionsRequest,
)
from app.shared.contracts.analysis import AgentType


class _DelegationToolRequest(DelegationRequest):
    """框架注入运行时；业务请求字段及约束继承自 DelegationRequest。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    runtime: ToolRuntime


def create_delegation_tools(service: AgentSessionService) -> list[BaseTool]:
    """创建绑定当前用户会话的委派、查询和删除工具。"""

    @tool("delegation", args_schema=_DelegationToolRequest)
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
        """创建或恢复专业 Agent Session，返回执行状态、Session 身份和原始文本回答。"""
        request = DelegationRequest.model_construct(
            analysis_id=analysis_id,
            agent_type=agent_type,
            session_id=session_id,
            message=message,
        )
        delegation_id = runtime.tool_call_id
        if delegation_id is None:
            raise RuntimeError("delegation 工具缺少 tool_call_id")
        try:
            result = await service.execute_delegation(
                request,
                cast(RunnableConfig, runtime.config),
                delegation_id=delegation_id,
                activity_writer=runtime.stream_writer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("执行专业 Agent 委派失败")
            return tool_error("专业 Agent 委派失败", exc, code="delegation_failed")
        return result.model_dump(mode="json")

    @tool("list_sessions", args_schema=ListSessionsRequest)
    async def list_sessions(
        analysis_id: Annotated[
            str | None,
            "可选分析标识；省略时查询当前 Conversation 的全部专业 Session",
        ] = None,
    ) -> dict[str, object]:
        """查询已有专业 Agent Session 的最新持久化状态。"""
        try:
            result = await service.list_sessions(analysis_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("查询专业 Agent Session 失败")
            return tool_error("Session 查询失败", exc, code="list_sessions_failed")
        return result.model_dump(mode="json")

    @tool("delete_session", args_schema=DeleteSessionRequest)
    async def delete_session(
        analysis_id: Annotated[str, "待删除 Session 所属分析标识"],
        agent_type: Annotated[AgentType, "待删除的专业 Agent 类型"],
        session_id: Annotated[str, "待删除的专业 Session 标识"],
    ) -> dict[str, object]:
        """幂等删除专业 Agent Session 的 Checkpoint 和沙箱资源。"""
        request = DeleteSessionRequest.model_construct(
            analysis_id=analysis_id,
            agent_type=agent_type,
            session_id=session_id,
        )
        try:
            result = await service.delete_session(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("删除专业 Agent Session 失败")
            return tool_error("Session 删除失败", exc, code="delete_session_failed")
        return result.model_dump(mode="json")

    return [delegation, list_sessions, delete_session]
