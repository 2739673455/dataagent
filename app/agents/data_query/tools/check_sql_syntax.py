"""SQL 语法、安全与资产权限检查工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from loguru import logger

from app.agents.data_query.tools.query_support import (
    build_query_guard,
    get_query_session,
)
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.entities.query import QueryDialect


@tool
async def check_sql_syntax(
    runtime: ToolRuntime,
    sql: Annotated[str, "需要检查的单条 Doris/MySQL 只读 SQL"],
    dialect: Annotated[QueryDialect, "SQL 输入方言"] = "doris",
) -> dict[str, Any]:
    """检查 SQL 语法、只读约束、元数据引用、JOIN、类型与当前用户权限"""
    try:
        session_key = get_query_session(runtime)
        async with meta_postgres_client_manager.session() as session:
            result = await build_query_guard(session).check(
                session_key.user_id,
                sql,
                dialect,
            )
    except Exception:  # noqa: BLE001
        logger.exception("SQL validation tool failed")
        return {
            "status": "error",
            "code": "sql_validation_failed",
            "message": "SQL validation is temporarily unavailable",
        }
    return {
        "status": "success" if result.valid else "error",
        **result.model_dump(mode="json"),
    }
