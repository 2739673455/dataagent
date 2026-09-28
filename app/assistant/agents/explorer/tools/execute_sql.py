"""Explorer 受控只读 SQL 执行工具。"""

from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.query.errors import QueryRejectedError, classify_query_error
from app.query.services.execution_handler import QueryExecutionHandler
from app.shared.contracts.analysis import AgentSessionKey


async def _execute_sql(
    handler: QueryExecutionHandler,
    runtime: ToolRuntime,
    sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
    purpose: Annotated[str, "本次 SQL 的具体查询目的"],
) -> dict[str, Any]:
    """安全执行只读 SQL，将完整结果写入当前会话 CSV 并返回紧凑摘要。"""
    try:
        configurable = runtime.config.get("configurable", {})
        session_key = AgentSessionKey(
            user_id=configurable["user_id"],
            conversation_id=UUID(configurable["conversation_id"]),
            analysis_id=configurable["analysis_id"],
            agent_type="explorer",
            session_id=configurable["session_id"],
        )
        result = await handler.execute(
            session_key,
            sql,
            purpose=purpose.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        code = classify_query_error(exc)
        response: dict[str, Any] = {"status": "error", "code": code}
        if isinstance(exc, QueryRejectedError):
            response.update(
                message="SQL 在提交 Doris 执行前未通过校验",
                hint="请根据 validation.issues 修正 SQL，然后再次调用 execute_sql",
                validation=exc.result.model_dump(mode="json"),
            )
        else:
            response.update(
                message="只读查询执行失败"
                if code == "readonly_query_failed"
                else str(exc),
                details=[
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            )
        return response
    return {"status": "success", **result.model_dump(mode="json")}


def create_execute_sql_tool(handler: QueryExecutionHandler) -> BaseTool:
    """使用查询用例处理器构建只读 SQL 工具。"""

    @tool("execute_sql")
    async def execute_sql_tool(
        runtime: ToolRuntime,
        sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
        purpose: Annotated[str, "本次 SQL 的具体查询目的"],
    ) -> dict[str, Any]:
        """安全执行只读 SQL 并写入会话产物。"""
        return await _execute_sql(
            handler,
            runtime,
            sql,
            purpose,
        )

    return execute_sql_tool
