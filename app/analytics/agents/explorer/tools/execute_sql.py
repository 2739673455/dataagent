"""Explorer 受控只读 SQL 执行工具"""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.authorization import AuthorizationService
from app.identity.services.credential import DorisCredentialCipher
from app.metadata.repositories.postgres import MetaPGRepo
from app.query.models.execution import (
    QueryExecutionLimits,
    QueryExecutionOptions,
    QueryExecutionStatus,
)
from app.query.models.validation import (
    QueryDialect,
    QueryValidationResult,
)
from app.query.providers import build_query_experience_service
from app.query.repositories.doris import DorisQueryRepository
from app.query.services.executor import (
    AnalysisQueryService,
    QueryArtifactStore,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryResultLimitExceededError,
    QueryResultShapeError,
    SuccessfulQueryExecution,
)
from app.query.services.experience import (
    QueryExecutionContext,
)
from app.query.services.guard import QueryGuardService, QueryRejectedError
from app.query.services.principal import QueryPrincipalService
from app.shared.clients.doris_client_manager import query_doris_client_registry
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.analysis import AgentSessionKey


def _get_query_session(runtime: ToolRuntime) -> AgentSessionKey:
    """从工具运行配置中读取并校验 Explorer Session"""
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


def _build_query_guard(
    meta_session: AsyncSession,
    auth_session: AsyncSession,
) -> QueryGuardService:
    """使用 PostgreSQL 会话构造目录和授权查询守卫"""
    return QueryGuardService(
        MetaPGRepo(meta_session),
        data_source=cfg.query.data_source,
        current_database=cfg.doris.database,
        policy_provider=AuthorizationService(AuthPGRepo(auth_session)),
    )


def _query_purpose(runtime: ToolRuntime, purpose: str | None) -> str:
    """读取显式查询目的或当前 Explorer 任务"""
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


async def _record_success_safely(
    context: QueryExecutionContext,
    details: SuccessfulQueryExecution,
) -> None:
    """记录成功查询，持久化故障不改变查询结果"""
    try:
        async with meta_postgres_client_manager.session() as session:
            await build_query_experience_service(session).record_success(context, details)
    except Exception:  # noqa: BLE001
        logger.exception("记录成功查询历史失败")


async def _record_failure_safely(
    context: QueryExecutionContext | None,
    *,
    raw_sql: str,
    dialect: QueryDialect,
    status: QueryExecutionStatus,
    error_code: str,
    error_detail: str,
    validation: QueryValidationResult | None = None,
) -> None:
    """记录失败查询，持久化故障不覆盖原始错误"""
    if context is None:
        return
    try:
        async with meta_postgres_client_manager.session() as session:
            await build_query_experience_service(session).record_failure(
                context,
                raw_sql=raw_sql,
                dialect=dialect,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                validation=validation,
            )
    except Exception:  # noqa: BLE001
        logger.exception("记录失败查询历史失败")


async def _execute_sql(
    artifact_store: QueryArtifactStore,
    runtime: ToolRuntime,
    sql: Annotated[str, "需要执行的单条 Doris/MySQL 只读 SQL"],
    purpose: Annotated[str | None, "本次 SQL 要解决的具体数据问题"] = None,
    dialect: Annotated[QueryDialect, "SQL 输入方言"] = "doris",
) -> dict[str, Any]:
    """安全执行只读 SQL，将完整结果写入当前会话 CSV 并返回紧凑摘要"""
    session_key: AgentSessionKey | None = None
    execution_context: QueryExecutionContext | None = None
    try:
        session_key = _get_query_session(runtime)
        async with (
            auth_postgres_client_manager.session() as auth_session,
            meta_postgres_client_manager.session() as meta_session,
        ):
            principal = await QueryPrincipalService(
                AuthPGRepo(auth_session),
                DorisQueryIdentityPGRepo(auth_session),
                DorisCredentialCipher(
                    cfg.doris_credentials.encryption_key.get_secret_value()
                ),
            ).resolve(session_key.user_id)
            context = QueryExecutionContext(
                session_key=session_key,
                role_name=principal.role_name,
                purpose=_query_purpose(runtime, purpose),
                tool_call_id=runtime.tool_call_id,
            )
            execution_context = context
            logger.info(
                f"已选择只读查询主体: user_id={session_key.user_id}, "
                f"doris_role={principal.role_name}, "
                f"doris_user={principal.query_user}"
            )
            limits = QueryExecutionLimits(
                workload_group=principal.workload_group,
                timeout_seconds=cfg.query.timeout_seconds,
                memory_limit_bytes=cfg.query.memory_limit_bytes,
                max_rows=cfg.query.max_rows,
                max_output_bytes=cfg.query.max_output_bytes,
            )
            options = QueryExecutionOptions(
                batch_size=cfg.query.batch_size,
                sample_rows=cfg.query.sample_rows,
            )
            service = AnalysisQueryService(
                _build_query_guard(meta_session, auth_session),
                DorisQueryRepository(
                    await query_doris_client_registry.get_or_create(
                        principal.role_name,
                        principal.query_user,
                        principal.password,
                    )
                ),
                artifact_store,
                limits,
                options,
                success_observer=lambda details: _record_success_safely(
                    context,
                    details,
                ),
            )
            result = await service.execute(
                session_key,
                sql,
                dialect,
            )
    except QueryRejectedError as exc:
        await _record_failure_safely(
            execution_context,
            raw_sql=sql,
            dialect=dialect,
            status="rejected",
            error_code="sql_validation_failed",
            error_detail=str(exc),
            validation=exc.result,
        )
        logger.warning(
            "只读查询在执行前被拒绝: "
            f"user_id={session_key.user_id if session_key else None}, "
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
        QueryOutputLimitExceededError,
        QueryPlanUnavailableError,
        QueryResultLimitExceededError,
        QueryResultShapeError,
    ) as exc:
        await _record_failure_safely(
            execution_context,
            raw_sql=sql,
            dialect=dialect,
            status="failed",
            error_code="query_result_rejected",
            error_detail=str(exc),
        )
        logger.warning(
            "只读查询结果校验未通过: "
            f"user_id={session_key.user_id if session_key else None}, "
            f"conversation_id={session_key.conversation_id if session_key else None}, "
            f"error_type={type(exc).__name__}"
        )
        return {
            "status": "error",
            "code": "query_result_rejected",
            "message": str(exc),
        }
    except Exception:  # noqa: BLE001
        logger.exception(
            "只读查询工具执行失败: "
            f"user_id={session_key.user_id if session_key else None}, "
            f"conversation_id={session_key.conversation_id if session_key else None}"
        )
        await _record_failure_safely(
            execution_context,
            raw_sql=sql,
            dialect=dialect,
            status="failed",
            error_code="readonly_query_failed",
            error_detail="只读查询执行失败",
        )
        return {
            "status": "error",
            "code": "readonly_query_failed",
            "message": "只读查询执行失败",
        }
    return {"status": "success", **result.model_dump(mode="json")}


def create_execute_sql_tool(artifact_store: QueryArtifactStore) -> BaseTool:
    """使用显式产物存储构建只读 SQL 工具"""

    @tool("execute_sql")
    async def execute_sql_tool(
        runtime: ToolRuntime,
        sql: Annotated[str, "需要执行的单条 Doris/MySQL 只读 SQL"],
        purpose: Annotated[str | None, "本次 SQL 要解决的具体数据问题"] = None,
        dialect: Annotated[QueryDialect, "SQL 输入方言"] = "doris",
    ) -> dict[str, Any]:
        """安全执行只读 SQL 并写入会话产物"""
        return await _execute_sql(
            artifact_store,
            runtime,
            sql,
            purpose,
            dialect,
        )

    return execute_sql_tool
