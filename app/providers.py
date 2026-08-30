"""应用运行时依赖组装入口"""

from app.analytics.agents.manager import AgentManager
from app.analytics.agents.skills import packaged_agent_skill_mounts
from app.analytics.providers import build_conversation_lifecycle_service
from app.analytics.services.conversation_run import ConversationRunService
from app.analytics.services.conversation_tombstone import (
    ConversationTombstoneService,
)
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.shared.clients.postgres_client_manager import (
    analytics_postgres_client_manager,
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.workflows.user_deletion import UserDeletionService

sandbox_manager = create_sandbox_manager(
    cfg.sandbox,
    packaged_agent_skill_mounts(),
)
conversation_tombstone_service = ConversationTombstoneService(
    analytics_postgres_client_manager
)
agent_manager = AgentManager(
    langgraph_postgres_manager,
    sandbox_manager,
    conversation_tombstone_service,
)
conversation_run_service = ConversationRunService(agent_manager)
conversation_lifecycle_service = build_conversation_lifecycle_service(
    langgraph_postgres_manager,
    analytics_postgres_client_manager,
    meta_postgres_client_manager,
    agent_manager,
    sandbox_manager,
    cfg.lifecycle,
)
user_deletion_service = UserDeletionService(
    PostgresUserDeletionStateStore(auth_postgres_client_manager),
    sandbox_manager,
    conversation_lifecycle_service,
    cfg.lifecycle,
)
