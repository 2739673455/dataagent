"""Analytics 应用服务依赖组装"""

from app.analytics.agents.manager import AgentManager
from app.analytics.repositories.conversation import ConversationPGRepo
from app.analytics.services.conversation_lifecycle import ConversationLifecycleService
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.config.app_config import LifecycleConfig


def build_conversation_lifecycle_service(
    persistence: LangGraphPostgresManager,
    agents: AgentManager,
    sandbox: DockerSandboxManager,
    config: LifecycleConfig,
    *,
    session_lock_timeout: float,
) -> ConversationLifecycleService:
    """组装会话跨存储生命周期服务"""
    return ConversationLifecycleService(
        lambda: ConversationPGRepo(persistence.get_store()),
        lambda: SemanticRecallPGRepo(persistence.get_store()),
        persistence,
        agents,
        sandbox,
        config,
        session_lock_timeout=session_lock_timeout,
    )
