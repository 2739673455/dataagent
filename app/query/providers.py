"""查询应用服务依赖组装。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.runtime import DatabaseQueryExecutionRuntime
from app.query.services.execution_handler import QueryExecutionHandler
from app.query.services.execution_recorder import QueryExecutionRecorder
from app.query.services.executor import QueryArtifactStore
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.postgres_client_manager import PostgresClientManager


def build_query_execution_recorder(session: AsyncSession) -> QueryExecutionRecorder:
    """创建查询执行审计服务。"""
    return QueryExecutionRecorder(QueryExecutionPGRepo(session))


def build_query_execution_handler(
    artifact_store: QueryArtifactStore,
    auth: PostgresClientManager,
    meta: PostgresClientManager,
    query_clients: DorisQueryClientRegistry,
    admin_doris: DorisClientManager,
) -> QueryExecutionHandler:
    """组装身份解析、受控执行和历史记录完整查询用例。"""
    return QueryExecutionHandler(
        DatabaseQueryExecutionRuntime(
            artifact_store,
            build_query_execution_recorder,
            auth,
            meta,
            query_clients,
            admin_doris,
        )
    )
