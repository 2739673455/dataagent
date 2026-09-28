"""专业 Agent Session 查询工具。"""

from langchain.tools import tool
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.execution.session_service import AgentSessionService
from app.assistant.execution.types import ListSessionsRequest


def create_list_sessions_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前用户 Conversation 的 Session 查询 Tool。"""

    @tool("list_sessions", args_schema=ListSessionsRequest)
    async def list_sessions(analysis_id: str | None = None) -> dict[str, object]:
        """查询已有专业 Agent Session 的最新持久化状态。"""
        try:
            result = await service.list_sessions(analysis_id)
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
