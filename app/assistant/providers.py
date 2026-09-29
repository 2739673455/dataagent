"""Assistant 应用服务依赖组装。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.services.lifecycle import ConversationLifecycleService
from app.assistant.services.manager import AgentManager
from app.assistant.services.run import ConversationRunService
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager


@asynccontextmanager
async def _conversation_repository(
    postgres: PostgresClientManager,
) -> AsyncGenerator[ConversationPGRepo]:
    """创建带事务边界的会话目录数据访问。"""
    async with postgres.session() as session, session.begin():
        yield ConversationPGRepo(session)


def build_conversation_lifecycle_service(
    persistence: LangGraphPostgresManager,
    assistant_postgres: PostgresClientManager,
    agents: AgentManager,
    sandbox: DockerSandboxManager,
    runs: ConversationRunService | None = None,
) -> ConversationLifecycleService:
    """组装会话跨存储生命周期服务。"""
    return ConversationLifecycleService(
        lambda: _conversation_repository(assistant_postgres),
        persistence,
        agents,
        sandbox,
        runs,
    )
