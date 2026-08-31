"""Explorer 受控只读 SQL 执行工具。"""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from loguru import logger

from app.query.models.execution import QueryExecutionTimeoutError
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.executor import (
    QueryPlanUnavailableError,
    QueryRejectedError,
    QueryResultShapeError,
)
from app.shared.contracts.analysis import AgentSessionKey


def _get_query_session(runtime: ToolRuntime) -> AgentSessionKey:
    """从工具运行配置中读取并校验 Explorer Session。"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    analysis_id = configurable.get("analysis_id")
    session_id = configurable.get("session_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("配置中未找到查询上下文")
    if not isinstance(analysis_id, str) or not isinstance(session_id, str):
        raise TypeError("配置中未找到专家会话上下文")
    return AgentSessionKey(
        user_id=user_id,
        conversation_id=UUID(raw_conversation_id),
        analysis_id=analysis_id,
        agent_type="explorer",
        session_id=session_id,
    )


def _query_purpose(runtime: ToolRuntime, purpose: str | None) -> str:
    """读取显式查询目的或当前 Explorer 任务。"""
    if purpose is not None and purpose.strip():
        return purpose.strip()[:20_000]
    state = runtime.state
    if isinstance(state, Mapping):
        messages = state.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if (
                    isinstance(message, HumanMessage)
                    and not message.additional_kwargs.get("dataagent_internal_retry")
                    and isinstance(message.content, str)
                    and message.content.strip()
                ):
                    return message.content.strip()[:20_000]
    return "执行只读数据查询"


def _error_details(error: Exception) -> list[dict[str, str]]:
    """构造可供模型处理的异常类别和原因。"""
    return [
        {
            "type": type(error).__name__,
            "msg": str(error).strip() or "异常未提供详情",
        }
    ]


async def _execute_sql(
    handler: QueryExecutionHandler,
    runtime: ToolRuntime,
    sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
    purpose: Annotated[str | None, "本次 SQL 要解决的具体数据问题"] = None,
) -> dict[str, Any]:
    """安全执行只读 SQL，将完整结果写入当前会话 CSV 并返回紧凑摘要。"""
    session_key: AgentSessionKey | None = None
    try:
        session_key = _get_query_session(runtime)
        result = await handler.execute(
            session_key,
            sql,
            purpose=_query_purpose(runtime, purpose),
            tool_call_id=runtime.tool_call_id,
        )
    except QueryRejectedError as exc:
        logger.warning(
            "只读查询在执行前被拒绝: "
            f"conversation_id={session_key.conversation_id if session_key else None}, "
            f"issue_count={len(exc.result.issues)}"
        )
        return {
            "status": "error",
            "code": "sql_validation_failed",
            "message": "SQL 在提交 Doris 执行前未通过校验",
            "hint": "请根据 validation.issues 修正 SQL，然后再次调用 execute_sql",
            "validation": exc.result.model_dump(mode="json"),
        }
    except (
        QueryExecutionTimeoutError,
        QueryPlanUnavailableError,
        QueryResultShapeError,
    ) as exc:
        logger.warning(
            "只读查询结果校验未通过: "
            f"conversation_id={session_key.conversation_id if session_key else None}, "
            f"error_type={type(exc).__name__}"
        )
        return {
            "status": "error",
            "code": "query_result_rejected",
            "message": str(exc),
            "details": _error_details(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "只读查询工具执行失败: "
            f"conversation_id={session_key.conversation_id if session_key else None}"
        )
        return {
            "status": "error",
            "code": "readonly_query_failed",
            "message": "只读查询执行失败",
            "details": _error_details(exc),
        }
    return {"status": "success", **result.model_dump(mode="json")}


def create_execute_sql_tool(handler: QueryExecutionHandler) -> BaseTool:
    """使用查询用例处理器构建只读 SQL 工具。"""

    @tool("execute_sql")
    async def execute_sql_tool(
        runtime: ToolRuntime,
        sql: Annotated[str, "需要执行的单条 Doris 只读 SQL"],
        purpose: Annotated[str | None, "本次 SQL 要解决的具体数据问题"] = None,
    ) -> dict[str, Any]:
        """安全执行只读 SQL 并写入会话产物。"""
        return await _execute_sql(
            handler,
            runtime,
            sql,
            purpose,
        )

    return execute_sql_tool
