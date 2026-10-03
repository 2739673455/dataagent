"""Explorer 受控只读 SQL 执行工具。"""

from typing import Annotated, Any, cast
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.agents.tools.errors import tool_error
from app.query import QueryExecutionService
from app.query.contracts import QueryExecutionScope
from app.query.errors import QueryRejectedError


def create_execute_sql_tool(service: QueryExecutionService) -> BaseTool:
    """使用查询用例处理器构建只读 SQL 工具。"""

    @tool("execute_sql")
    async def execute_sql_tool(
        runtime: ToolRuntime,
        sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
        purpose: Annotated[str, "本次 SQL 的具体查询目的"],
    ) -> dict[str, Any]:
        """安全执行只读 SQL 并写入会话产物。"""
        configurable = cast(dict[str, Any], runtime.config)["configurable"]
        session_key = QueryExecutionScope(
            user_id=configurable["user_id"],
            conversation_id=UUID(configurable["conversation_id"]),
            analysis_id=configurable["analysis_id"],
            agent_type="explorer",
            session_id=configurable["session_id"],
        )
        try:
            result = await service.execute(
                session_key,
                sql,
                purpose=purpose,
                tool_call_id=runtime.tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, QueryRejectedError):
                logger.warning(
                    f"SQL 校验未通过: conversation_id={session_key.conversation_id}, {exc}"
                )
                return tool_error(
                    "SQL 在提交 Doris 执行前未通过校验",
                    f"{exc}。请根据上述问题修正 SQL，然后再次调用 execute_sql。",
                )
            logger.exception(
                f"只读查询工具执行失败: conversation_id={session_key.conversation_id}"
            )
            return tool_error("只读查询执行失败", exc)
        return {"status": "success", **result.model_dump(mode="json")}

    return execute_sql_tool
