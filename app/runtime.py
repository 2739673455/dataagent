"""Web 进程资源所有权与启动装配；每次 lifespan 创建独立实例。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from loguru import logger

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.tombstones import ConversationTombstoneStore
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.execution.runtime_factory import ConversationAgentRuntimeFactory
from app.assistant.providers import build_conversation_lifecycle_service
from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.repositories.identity import IdentityPGRepo
from app.identity.services.credential import DorisCredentialCipher
from app.identity.services.rate_limit import AuthRateLimitService
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.query.providers import build_query_execution_handler
from app.query.repositories.doris import DorisQueryRepository
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, AuthBase, MetaBase
from app.workflows.user_deletion import UserDeletionService


@dataclass(frozen=True, slots=True)
class WebResources:
    """仅供启动入口和 HTTP 依赖组装使用，不传入业务服务。"""

    auth: PostgresClientManager
    meta: PostgresClientManager
    assistant: PostgresClientManager
    admin_doris: DorisClientManager
    query_clients: DorisQueryClientRegistry
    embedding: EmbeddingClientManager
    es: ESClientManager
    persistence: LangGraphPostgresManager
    sandbox: DockerSandboxManager
    agents: AgentManager
    runs: ConversationRunService
    conversations: ConversationLifecycleService
    user_deletion: UserDeletionService
    recall: SemanticRecallRuntime
    auth_rate_limit: AuthRateLimitService


def _create_resources() -> WebResources:
    """组装当前 lifespan 的资源，联网初始化由 lifespan 执行。"""
    auth = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    meta = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    assistant = PostgresClientManager(cfg.langgraph_postgresql, AssistantBase)
    admin_doris = DorisClientManager(cfg.doris)
    query_clients = DorisQueryClientRegistry(cfg.doris)
    embedding = EmbeddingClientManager(cfg.embedding)
    es = ESClientManager(cfg.elasticsearch)
    persistence = LangGraphPostgresManager(cfg.langgraph_postgresql)
    sandbox = create_sandbox_manager(cfg.sandbox, packaged_skill_readonly_mounts())
    tombstones = ConversationTombstoneStore(assistant)
    recall = SemanticRecallRuntime(auth, meta, embedding, es)
    factory = ConversationAgentRuntimeFactory(
        persistence,
        sandbox,
        recall,
        build_query_execution_handler(sandbox, auth, meta, query_clients),
    )
    agents = AgentManager(persistence, tombstones, factory)
    runs = ConversationRunService(agents, sandbox, recall, persistence)
    conversations = build_conversation_lifecycle_service(
        persistence,
        assistant,
        meta,
        agents,
        sandbox,
        cfg.lifecycle,
        runs,
    )
    return WebResources(
        auth=auth,
        meta=meta,
        assistant=assistant,
        admin_doris=admin_doris,
        query_clients=query_clients,
        embedding=embedding,
        es=es,
        persistence=persistence,
        sandbox=sandbox,
        agents=agents,
        runs=runs,
        conversations=conversations,
        user_deletion=UserDeletionService(
            PostgresUserDeletionStateStore(auth),
            sandbox,
            conversations,
            cfg.lifecycle,
        ),
        recall=recall,
        auth_rate_limit=AuthRateLimitService(
            redis_url=cfg.auth.rate_limit_redis_url.get_secret_value(),
        ),
    )


async def _verify_doris_query_identities(resources: WebResources) -> None:
    """校验数据库中全部查询身份的 Doris 权限。"""
    cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    async with resources.auth.session() as session:
        identities = await IdentityPGRepo(session).list_query_identities()
    try:
        await DorisRoleRepository(resources.admin_doris).verify_configured_roles(
            tuple(identity.role_name for identity in identities)
        )
    except Exception as exc:  # noqa: BLE001
        # 管理员需要应用保持可用以修复 Doris 侧配置；实际查询仍会在身份解析和
        # Doris 权限边界失败，因此启动检查只负责暴露漂移，不放宽查询权限。
        logger.warning(f"Doris 查询角色完整性校验未通过，应用继续启动: {exc}")
    for identity in identities:
        try:
            manager = await resources.query_clients.get_or_create(
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
async def lifespan(app: FastAPI):
    """启动时创建资源，失败及退出时逆序清理，避免应用实例之间共享连接。"""
    resources = _create_resources()
    async with AsyncExitStack() as stack:
        stack.callback(resources.auth_rate_limit.close)
        for resource in (
            resources.query_clients,
            resources.admin_doris,
            resources.auth,
            resources.meta,
            resources.assistant,
            resources.es,
            resources.embedding,
            resources.persistence,
            resources.sandbox,
            resources.agents,
            resources.runs,
        ):
            stack.push_async_callback(resource.close)
        logger.info("开始初始化应用资源")
        resources.embedding.init()
        resources.es.init()
        await resources.persistence.init()
        await resources.sandbox.init()
        for postgres in (resources.auth, resources.meta, resources.assistant):
            postgres.init()
            await postgres.init_tables()
        resources.admin_doris.init()
        await resources.agents.init()
        await _verify_doris_query_identities(resources)
        logger.info("应用资源初始化完成")
        app.state.resources = resources
        try:
            yield
        finally:
            del app.state.resources
