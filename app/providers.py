"""应用运行时依赖组装入口。"""

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.agents.manager import AgentManager
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.services.conversation_run import ConversationRunService
from app.assistant.services.conversation_tombstone_store import (
    ConversationTombstoneStore,
)
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.shared.clients.postgres_client_manager import (
    assistant_postgres_client_manager,
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.workflows.user_deletion import UserDeletionService

sandbox_manager = create_sandbox_manager(
    cfg.sandbox,
    packaged_skill_readonly_mounts(),
)
conversation_tombstone_store = ConversationTombstoneStore(
    assistant_postgres_client_manager
)
agent_manager = AgentManager(
    langgraph_postgres_manager,
    sandbox_manager,
    conversation_tombstone_store,
)
conversation_run_service = ConversationRunService(agent_manager, sandbox_manager)
conversation_lifecycle_service = build_conversation_lifecycle_service(
    langgraph_postgres_manager,
    assistant_postgres_client_manager,
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
