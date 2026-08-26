"""应用运行时依赖组装入口"""

from app.analytics.agents.manager import AgentManager
from app.analytics.providers import build_conversation_lifecycle_service
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.query.services.user_cleanup import QueryHistoryCleanupService
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.workflows.user_deletion import UserDeletionService

sandbox_manager = create_sandbox_manager(cfg.sandbox)
agent_manager = AgentManager(langgraph_postgres_manager, sandbox_manager)
conversation_lifecycle_service = build_conversation_lifecycle_service(
    langgraph_postgres_manager,
    agent_manager,
    sandbox_manager,
    cfg.lifecycle,
    session_lock_timeout=cfg.agent.orchestration.session_lock_timeout,
)
user_deletion_service = UserDeletionService(
    PostgresUserDeletionStateStore(auth_postgres_client_manager),
    QueryHistoryCleanupService(meta_postgres_client_manager, es_client_manager),
    sandbox_manager,
    conversation_lifecycle_service,
    cfg.lifecycle,
)
