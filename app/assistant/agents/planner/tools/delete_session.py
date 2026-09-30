"""专业 Agent Session 删除工具。"""

from typing import Annotated

from langchain.tools import tool
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.agents.tools.errors import tool_error
from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import DeleteSessionRequest
from app.shared.contracts.analysis import AgentType


def create_delete_session_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前用户 Conversation 的 Session 删除 Tool。"""

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

    return delete_session
