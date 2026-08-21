"""Explorer 受控只读 SQL 执行工具"""

from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentSessionKey, validate_agent_type
from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.clients.doris_client_manager import query_doris_client_registry
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.conf.app_config import cfg
from app.models.meta import QueryExecutionStatus
from app.models.query import QueryDialect, QueryExecutionLimits, QueryValidationResult
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_query_identity_pg_repo import DorisQueryIdentityPGRepo
from app.repositories.doris_query_repo import DorisQueryRepository
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.query_experience_es_repo import QueryExperienceESRepo
from app.repositories.query_experience_pg_repo import QueryExperiencePGRepo
from app.services.analysis_query_service import (
    AnalysisQueryService,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryResultLimitExceededError,
    QueryResultShapeError,
    QueryScanLimitExceededError,
    SuccessfulQueryExecution,
)
from app.services.authorization_service import AuthorizationService
from app.services.doris_credential_service import DorisCredentialCipher
from app.services.query_experience_service import (
    QueryExecutionContext,
    QueryExperienceService,
)
from app.services.query_guard_service import QueryGuardService, QueryRejectedError
from app.services.query_principal_service import QueryPrincipalService


def _get_query_session(runtime: ToolRuntime) -> AgentSessionKey:
    """从工具运行配置中读取并校验 Explorer Session"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    analysis_id = configurable.get("analysis_id")
    raw_agent_type = configurable.get("agent_type")
    session_id = configurable.get("session_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("query context not found in config")
    if not isinstance(analysis_id, str) or not isinstance(session_id, str):
        raise TypeError("query specialist session not found in config")
    if not isinstance(raw_agent_type, str):
        raise TypeError("query agent type not found in config")
    agent_type = validate_agent_type(raw_agent_type)
    if agent_type != "explorer":
        raise ValueError("SQL tools require an explorer session")
    return AgentSessionKey(
        user_id=user_id,
        conversation_id=UUID(raw_conversation_id),
        analysis_id=analysis_id,
        agent_type=agent_type,
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
        max_cell_bytes=cfg.query.max_cell_bytes,
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


def _query_experience_service(meta_session: AsyncSession) -> QueryExperienceService:
    """构造查询经验记录与检索服务"""
    return QueryExperienceService(
        QueryExperiencePGRepo(meta_session),
        QueryExperienceESRepo(es_client_manager.get_client()),
        embedding_client_manager.get_client(),
        data_source=cfg.query.data_source,
        database_name=cfg.doris.database,
    )


async def _record_success_safely(
    context: QueryExecutionContext,
    details: SuccessfulQueryExecution,
) -> None:
    """记录成功查询，持久化故障不改变查询结果"""
    try:
        async with meta_postgres_client_manager.session() as session:
            await _query_experience_service(session).record_success(context, details)
    except Exception:  # noqa: BLE001
        logger.exception("Successful query history persistence failed")


async def _record_failure_safely(
    context: QueryExecutionContext | None,
    session_key: AgentSessionKey | None,
    *,
    raw_sql: str,
    dialect: QueryDialect,
    status: QueryExecutionStatus,
    error_code: str,
    error_detail: str,
    validation: QueryValidationResult | None = None,
) -> None:
    """记录失败查询，持久化故障不覆盖原始错误"""
    if context is None or session_key is None:
        return
    try:
        async with meta_postgres_client_manager.session() as session:
            await _query_experience_service(session).record_failure(
                context,
                session_key,
                raw_sql=raw_sql,
                dialect=dialect,
                status=status,
                error_code=error_code,
                error_detail=error_detail,
                validation=validation,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed query history persistence failed")


@tool
async def execute_sql(
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
                user_id=session_key.user_id,
                role_name=principal.role_name,
                purpose=_query_purpose(runtime, purpose),
                tool_call_id=runtime.tool_call_id,
            )
            execution_context = context
            logger.info(
                "Readonly query principal selected: "
                f"user_id={session_key.user_id}, "
                f"doris_role={principal.role_name}, "
                f"doris_user={principal.query_user}"
            )
            limits = QueryExecutionLimits(
                workload_group=principal.workload_group,
                timeout_seconds=cfg.query.timeout_seconds,
                memory_limit_bytes=cfg.query.memory_limit_bytes,
                max_scan_rows=cfg.query.max_scan_rows,
                max_scan_bytes=cfg.query.max_scan_bytes,
                max_cell_bytes=cfg.query.max_cell_bytes,
                max_rows=cfg.query.max_rows,
                max_output_bytes=cfg.query.max_output_bytes,
                batch_size=cfg.query.batch_size,
                sample_rows=cfg.query.sample_rows,
                output_format=cfg.query.output_format,
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
                docker_sandbox_manager,
                limits,
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
            session_key,
            raw_sql=sql,
            dialect=dialect,
            status="rejected",
            error_code="sql_validation_failed",
            error_detail=str(exc),
            validation=exc.result,
        )
        return {
            "status": "error",
            "code": "sql_validation_failed",
            "message": "SQL validation failed before Doris execution",
            "hint": (
                "Revise the SQL according to validation.issues, then call "
                "execute_sql again"
            ),
            "validation": exc.result.model_dump(mode="json"),
        }
    except (
        QueryExecutionTimeoutError,
        QueryOutputLimitExceededError,
        QueryPlanUnavailableError,
        QueryResultLimitExceededError,
        QueryResultShapeError,
        QueryScanLimitExceededError,
    ) as exc:
        await _record_failure_safely(
            execution_context,
            session_key,
            raw_sql=sql,
            dialect=dialect,
            status="failed",
            error_code="query_result_rejected",
            error_detail=str(exc),
        )
        logger.warning(f"Readonly query result rejected: {type(exc).__name__}")
        return {
            "status": "error",
            "code": "query_result_rejected",
            "message": str(exc),
        }
    except Exception:  # noqa: BLE001
        logger.exception("Readonly query tool failed")
        await _record_failure_safely(
            execution_context,
            session_key,
            raw_sql=sql,
            dialect=dialect,
            status="failed",
            error_code="readonly_query_failed",
            error_detail="Readonly query execution failed",
        )
        return {
            "status": "error",
            "code": "readonly_query_failed",
            "message": "Readonly query execution failed",
        }
    return {"status": "success", **result.model_dump(mode="json")}
