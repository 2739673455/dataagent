"""Explorer 受控只读 SQL 执行工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from loguru import logger

from app.agents.explorer.tools.query_support import (
    build_query_guard,
    get_query_session,
)
from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.clients.doris_client_manager import query_doris_client_registry
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.app_config import cfg
from app.entities.query import QueryDialect, QueryExecutionLimits
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_query_repo import DorisQueryRepository
from app.services.analysis_query_service import (
    AnalysisQueryService,
    QueryExecutionTimeoutError,
    QueryOutputLimitExceededError,
    QueryPlanUnavailableError,
    QueryResultLimitExceededError,
    QueryResultShapeError,
    QueryScanLimitExceededError,
)
from app.services.query_guard_service import QueryRejectedError
from app.services.query_principal_service import QueryPrincipalService


@tool
async def execute_sql(
    runtime: ToolRuntime,
    sql: Annotated[str, "需要执行的单条 Doris/MySQL 只读 SQL"],
    dialect: Annotated[QueryDialect, "SQL 输入方言"] = "doris",
) -> dict[str, Any]:
    """安全执行只读 SQL，将完整结果写入当前会话 CSV 并返回紧凑摘要"""
    try:
        session_key = get_query_session(runtime)
        async with meta_postgres_client_manager.session() as meta_session:
            principal = await QueryPrincipalService(
                AuthPGRepo(meta_session),
                cfg.doris_roles,
            ).resolve(session_key.user_id)
            logger.info(
                "Readonly query principal selected: "
                f"user_id={session_key.user_id}, "
                f"doris_role={principal.role_name}, "
                f"doris_user={principal.config.query_user}"
            )
            limits = QueryExecutionLimits(
                workload_group=principal.config.workload_group,
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
                build_query_guard(meta_session),
                DorisQueryRepository(
                    query_doris_client_registry.get(principal.role_name)
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
