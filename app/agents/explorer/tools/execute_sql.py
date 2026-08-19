"""Explorer 受控只读 SQL 执行工具"""

from typing import Annotated, Any
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentSessionKey, validate_agent_type
from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.clients.doris_client_manager import query_doris_client_registry
from app.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.conf.app_config import cfg
from app.models.query import QueryDialect, QueryExecutionLimits
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_query_identity_pg_repo import DorisQueryIdentityPGRepo
from app.repositories.doris_query_repo import DorisQueryRepository
from app.repositories.meta_pg_repo import MetaPGRepo
from app.services.analysis_query_service import (
    AnalysisQueryService,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryResultLimitExceededError,
    QueryResultShapeError,
    QueryScanLimitExceededError,
)
from app.services.authorization_service import AuthorizationService
from app.services.doris_credential_service import DorisCredentialCipher
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


@tool
async def execute_sql(
    runtime: ToolRuntime,
    sql: Annotated[str, "需要执行的单条 Doris/MySQL 只读 SQL"],
    dialect: Annotated[QueryDialect, "SQL 输入方言"] = "doris",
) -> dict[str, Any]:
    """安全执行只读 SQL，将完整结果写入当前会话 CSV 并返回紧凑摘要"""
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
            )
            result = await service.execute(
                session_key,
                sql,
                dialect,
            )
    except QueryRejectedError as exc:
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
        logger.warning(f"Readonly query result rejected: {type(exc).__name__}")
        return {
            "status": "error",
            "code": "query_result_rejected",
            "message": str(exc),
        }
    except Exception:  # noqa: BLE001
        logger.exception("Readonly query tool failed")
        return {
            "status": "error",
            "code": "readonly_query_failed",
            "message": "Readonly query execution failed",
        }
    return {"status": "success", **result.model_dump(mode="json")}
