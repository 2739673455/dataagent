"""Explorer 受控只读 SQL 执行工具。"""

from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.query.errors import QueryRejectedError
from app.query.services.execution_handler import QueryExecutionHandler


def create_execute_sql_tool(handler: QueryExecutionHandler) -> BaseTool:
    """使用查询用例处理器构建只读 SQL 工具。"""

    @tool("execute_sql")
    async def execute_sql_tool(
        runtime: ToolRuntime,
        sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
        purpose: Annotated[str, "本次 SQL 的具体查询目的"],
    ) -> dict[str, Any]:
        """安全执行只读 SQL 并写入会话产物。"""
        try:
            configurable = runtime.config.get("configurable", {})
            result = await handler.execute(
                configurable["user_id"],
                UUID(configurable["conversation_id"]),
                sql,
                purpose=purpose.strip(),
            )
        except QueryRejectedError as exc:
            return {
                "status": "error",
                "message": "SQL 在提交 Doris 执行前未通过校验",
                "hint": "请根据 validation.issues 修正 SQL，然后再次调用 execute_sql",
                "validation": exc.result.model_dump(mode="json"),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "message": str(exc).strip() or "只读查询执行失败",
                "details": [
                    {
                        "type": type(exc).__name__,
                        "msg": str(exc).strip() or "异常未提供详情",
                    }
                ],
            }
        return {"status": "success", **result.model_dump(mode="json")}

    return execute_sql_tool
