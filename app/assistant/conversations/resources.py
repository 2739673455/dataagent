"""后台清理任务专用的会话资源生命周期。"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.checkpoints.postgres import PostgresCheckpointStore
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.tombstones import (
    ConversationTombstoneStore,
)
from app.assistant.execution.manager import AgentManager
from app.assistant.providers import build_conversation_lifecycle_service
from app.sandbox import DockerSandboxManager
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, MetaBase


@dataclass(frozen=True, slots=True)
class ConversationLifecycleResources:
    """清理任务使用的会话服务与沙箱。"""

    conversations: ConversationLifecycleService
    sandbox: DockerSandboxManager


@asynccontextmanager
async def conversation_lifecycle_resources() -> AsyncGenerator[
    ConversationLifecycleResources
]:
    """为一次后台清理创建隔离资源，并在任何退出路径尝试全部清理。"""
    async with AsyncExitStack() as stack:
        persistence = PostgresCheckpointStore(cfg.langgraph_postgresql)
        stack.push_async_callback(persistence.close)
        locks = PostgresAdvisoryLocks(cfg.langgraph_postgresql)
        stack.push_async_callback(locks.close)
        assistant_postgres = PostgresClientManager(
            cfg.langgraph_postgresql, AssistantBase
        )
        stack.push_async_callback(assistant_postgres.close)
        meta_postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
        stack.push_async_callback(meta_postgres.close)
        sandbox = DockerSandboxManager(
            cfg.sandbox,
            readonly_mounts=packaged_skill_readonly_mounts(),
        )
        stack.push_async_callback(sandbox.disconnect)
        agents = AgentManager(
            persistence, ConversationTombstoneStore(assistant_postgres), locks
        )
        stack.push_async_callback(agents.close)
        service = build_conversation_lifecycle_service(
            locks,
            assistant_postgres,
            meta_postgres,
            agents,
            sandbox,
            cfg.lifecycle,
        )
        await persistence.init()
        await locks.init()
        await sandbox.init(start_cleanup=False)
        yield ConversationLifecycleResources(service, sandbox)
