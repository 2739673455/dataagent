"""Planner 的 Session-aware 专业 Agent 委派工具"""

from typing import Annotated, cast

from langchain.tools import ToolRuntime, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import ValidationError

from app.analytics.agents.contracts import (
    DelegationRequest,
    DeleteSessionRequest,
    ListSessionsRequest,
)
from app.analytics.agents.session_service import AgentSessionService
from app.shared.contracts.analysis import AgentType


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
        repair_depth: Annotated[
            int,
            "当前修补链深度，普通委派为 0，沿 RepairRequest 委派时加一",
        ] = 0,
    ) -> dict[str, object]:
        """创建或恢复专业 Agent Session 并返回可验证的结构化结果"""
        try:
            request = DelegationRequest(
                analysis_id=analysis_id,
                agent_type=agent_type,
                session_id=session_id,
                message=message,
                repair_depth=repair_depth,
            )
        except ValidationError as exc:
            return {
                "status": "error",
                "code": "invalid_delegation_request",
                "message": "委派请求无效",
                "details": exc.errors(include_url=False),
            }
        try:
            result = await service.execute_delegation(
                request,
                cast(RunnableConfig, runtime.config),
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
        return result.model_dump(mode="json")

    return delegation


def create_list_sessions_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前用户 Conversation 的 Session 查询 Tool"""

    @tool("list_sessions")
    async def list_sessions(
        runtime: ToolRuntime,
        analysis_id: Annotated[
            str | None,
            "可选分析标识；省略时查询当前 Conversation 的全部专业 Session",
        ] = None,
    ) -> dict[str, object]:
        """查询已有专业 Agent Session 的最新持久化状态"""
        del runtime
        try:
            request = ListSessionsRequest(analysis_id=analysis_id)
        except ValidationError as exc:
            return {
                "status": "error",
                "code": "invalid_list_sessions_request",
                "message": "Session 查询请求无效",
                "details": exc.errors(include_url=False),
            }
        try:
            result = await service.list_sessions(request.analysis_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("查询专业 Agent Session 失败")
            return {
                "status": "error",
                "code": "list_sessions_failed",
                "message": "Session 查询失败",
                "details": [
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            }
        return result.model_dump(mode="json")

    return list_sessions


def create_delete_session_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前用户 Conversation 的 Session 删除 Tool"""

    @tool("delete_session")
    async def delete_session(
        runtime: ToolRuntime,
        analysis_id: Annotated[str, "待删除 Session 所属分析标识"],
        agent_type: Annotated[AgentType, "待删除的专业 Agent 类型"],
        session_id: Annotated[str, "待删除的专业 Session 标识"],
    ) -> dict[str, object]:
        """幂等删除专业 Agent Session 的 Checkpoint 和沙箱资源"""
        try:
            request = DeleteSessionRequest(
                analysis_id=analysis_id,
                agent_type=agent_type,
                session_id=session_id,
            )
        except ValidationError as exc:
            return {
                "status": "error",
                "code": "invalid_delete_session_request",
                "message": "Session 删除请求无效",
                "details": exc.errors(include_url=False),
            }
        try:
            result = await service.delete_session(
                request,
                cast(RunnableConfig, runtime.config),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("删除专业 Agent Session 失败")
            return {
                "status": "error",
                "code": "delete_session_failed",
                "message": "Session 删除失败",
                "details": [
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            }
        return result.model_dump(mode="json")

    return delete_session
