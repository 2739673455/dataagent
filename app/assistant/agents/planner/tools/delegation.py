"""专业 Agent 委派工具。"""

from typing import cast

from langchain.tools import ToolRuntime, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import (
    DelegationRequest,
)
from app.shared.contracts.analysis import AgentType


def create_delegation_tool(service: AgentSessionService) -> BaseTool:
    """创建只绑定当前用户会话的 delegation Tool。"""

    @tool("delegation", args_schema=DelegationRequest)
    async def delegation(
        runtime: ToolRuntime,
        analysis_id: str,
        agent_type: AgentType,
        session_id: str,
        message: str,
    ) -> str | dict[str, object]:
        """创建或续接专业 Agent Session，返回其文本回答和文件交付指令。"""
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
        return result.content

    return delegation
