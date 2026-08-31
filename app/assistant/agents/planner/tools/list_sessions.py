"""专业 Agent Session 查询工具。"""

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import ValidationError

from app.assistant.agents.contracts import ListSessionsRequest
from app.assistant.agents.session_service import AgentSessionService


def create_list_sessions_tool(service: AgentSessionService) -> BaseTool:
    """创建绑定当前用户 Conversation 的 Session 查询 Tool。"""

    @tool("list_sessions")
    async def list_sessions(
        runtime: ToolRuntime,
        analysis_id: Annotated[
            str | None,
            "可选分析标识；省略时查询当前 Conversation 的全部专业 Session",
        ] = None,
    ) -> dict[str, object]:
        """查询已有专业 Agent Session 的最新持久化状态。"""
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
