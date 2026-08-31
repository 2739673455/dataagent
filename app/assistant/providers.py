"""Assistant 应用服务依赖组装"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.assistant.agents.manager import AgentManager
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.conversation_lifecycle import ConversationLifecycleService
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import LifecycleConfig


@asynccontextmanager
async def _conversation_repository(
    postgres: PostgresClientManager,
) -> AsyncGenerator[ConversationPGRepo]:
    """创建带事务边界的会话目录数据访问"""
    async with postgres.session() as session, session.begin():
        yield ConversationPGRepo(session)


@asynccontextmanager
async def _semantic_recall_repository(
    postgres: PostgresClientManager,
) -> AsyncGenerator[SemanticRecallPGRepo]:
    """创建带事务边界的语义召回数据访问"""
    async with postgres.session() as session, session.begin():
        yield SemanticRecallPGRepo(session)


def build_conversation_lifecycle_service(
    persistence: LangGraphPostgresManager,
    assistant_postgres: PostgresClientManager,
    meta_postgres: PostgresClientManager,
    agents: AgentManager,
    sandbox: DockerSandboxManager,
    config: LifecycleConfig,
) -> ConversationLifecycleService:
    """组装会话跨存储生命周期服务"""
    return ConversationLifecycleService(
        lambda: _conversation_repository(assistant_postgres),
        lambda: _semantic_recall_repository(meta_postgres),
        persistence,
        agents,
        sandbox,
        config,
    )
