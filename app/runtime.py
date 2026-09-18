"""Web 进程运行时：组装应用级服务，统一初始化与释放资源。

模块级资源仅由本进程的 lifespan 持有；Celery 任务使用各自的任务资源。
"""

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.agents.manager import AgentManager
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.services.conversation_run import ConversationRunService
from app.assistant.services.conversation_tombstone_store import (
    ConversationTombstoneStore,
)
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.query.repositories.doris import DorisQueryRepository
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.doris_client_manager import (
    admin_doris_client_manager,
    query_doris_client_registry,
)
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
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


async def _verify_doris_query_identities() -> None:
    """校验数据库中全部查询身份的 Doris 权限。"""
    cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    async with auth_postgres_client_manager.session() as session:
        identities = await IdentityPGRepo(session).list_query_identities()
    try:
        await DorisRoleRepository(admin_doris_client_manager).verify_configured_roles(
            tuple(identity.role_name for identity in identities)
        )
    except Exception as exc:  # noqa: BLE001
        # 管理员需要应用保持可用以修复 Doris 侧配置；实际查询仍会在身份解析和
        # Doris 权限边界失败，因此启动检查只负责暴露漂移，不放宽查询权限。
        logger.warning(f"Doris 查询角色完整性校验未通过，应用继续启动: {exc}")
    for identity in identities:
        try:
            manager = await query_doris_client_registry.get_or_create(
                identity.role_name,
                identity.query_user,
                cipher.decrypt(identity.encrypted_password),
            )
            await DorisQueryRepository(manager).verify_readonly_access(
                identity.workload_group,
                cfg.doris.database,
                identity.role_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Doris 角色 '{identity.role_name}' 未完成目标库表授权或校验未通过，"
                f"应用继续启动: {exc}"
            )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """初始化并释放应用进程持有的共享资源。"""
    async with AsyncExitStack() as stack:
        stack.push_async_callback(query_doris_client_registry.close)
        stack.push_async_callback(admin_doris_client_manager.close)
        stack.push_async_callback(auth_postgres_client_manager.close)
        stack.push_async_callback(meta_postgres_client_manager.close)
        stack.push_async_callback(assistant_postgres_client_manager.close)
        stack.push_async_callback(es_client_manager.close)
        stack.push_async_callback(embedding_client_manager.close)
        stack.push_async_callback(langgraph_postgres_manager.close)
        stack.push_async_callback(sandbox_manager.close)
        stack.push_async_callback(agent_manager.close)
        stack.push_async_callback(conversation_run_service.close)
        # FastAPI 应用启动前执行。
        logger.info("开始初始化应用资源")
        embedding_client_manager.init()
        es_client_manager.init()
        await langgraph_postgres_manager.init()
        await sandbox_manager.init()
        await agent_manager.init()
        auth_postgres_client_manager.init()
        await auth_postgres_client_manager.init_tables()
        meta_postgres_client_manager.init()
        await meta_postgres_client_manager.init_tables()
        assistant_postgres_client_manager.init()
        await assistant_postgres_client_manager.init_tables()
        admin_doris_client_manager.init()
        await _verify_doris_query_identities()
        logger.info("应用资源初始化完成")

        yield
