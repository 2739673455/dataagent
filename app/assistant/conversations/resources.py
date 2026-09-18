"""后台清理任务专用的会话资源生命周期。"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.tombstones import (
    ConversationTombstoneStore,
)
from app.assistant.execution.manager import AgentManager
from app.assistant.providers import build_conversation_lifecycle_service
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
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
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    assistant_postgres = PostgresClientManager(
        cfg.langgraph_postgresql,
        AssistantBase,
    )
    meta_postgres = PostgresClientManager(
        cfg.meta_postgresql,
        MetaBase,
    )
    sandbox = create_sandbox_manager(
        cfg.sandbox,
        packaged_skill_readonly_mounts(),
    )
    agents = AgentManager(
        persistence,
        ConversationTombstoneStore(assistant_postgres),
    )
    service = build_conversation_lifecycle_service(
        persistence,
        assistant_postgres,
        meta_postgres,
        agents,
        sandbox,
        cfg.lifecycle,
    )
    async with AsyncExitStack() as stack:
        stack.push_async_callback(persistence.close)
        stack.push_async_callback(assistant_postgres.close)
        stack.push_async_callback(meta_postgres.close)
        stack.push_async_callback(sandbox.disconnect)
        stack.push_async_callback(agents.close)
        await persistence.init()
        assistant_postgres.init()
        meta_postgres.init()
        await sandbox.init(start_cleanup=False)
        yield ConversationLifecycleResources(service, sandbox)
