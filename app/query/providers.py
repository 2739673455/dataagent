"""查询应用服务依赖组装。"""

from app.query.runtime import DatabaseQueryExecutionRuntime
from app.query.services.execution_handler import QueryExecutionHandler
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.doris_client_manager import (
    DorisQueryClientRegistry,
)
from app.shared.clients.postgres_client_manager import PostgresClientManager


def build_query_execution_handler(
    artifact_store: DockerSandboxManager,
    auth: PostgresClientManager,
    query_clients: DorisQueryClientRegistry,
) -> QueryExecutionHandler:
    """组装身份解析和受控执行完整查询用例。"""
    return QueryExecutionHandler(
        DatabaseQueryExecutionRuntime(
            artifact_store,
            auth,
            query_clients,
        )
    )
