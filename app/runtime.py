"""Web 进程资源所有权与启动装配；每次 lifespan 创建独立实例。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI
from loguru import logger

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.application import (
    ConversationLifecycleService,
    SemanticRecallService,
)
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.repositories.checkpoint import PostgresCheckpointStore
from app.assistant.repositories.conversation_tombstone import ConversationTombstoneStore
from app.assistant.runtime import ConversationAgentRuntimeFactory
from app.assistant.services.agent_manager import AgentManager
from app.assistant.services.conversation_run import ConversationRunService
from app.identity.application import IdentityService, UserDeletionStateService
from app.identity.services.rate_limit import AuthRateLimitService
from app.metadata.application import MetadataReader, SemanticResourceService
from app.query.application import QueryExecutionService, QueryExperienceService
from app.sandbox.application import DockerSandboxManager
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, AuthBase, MetaBase, QueryBase
from app.workflows.application import UserDeletionService


@dataclass(frozen=True, slots=True)
class WebResources:
    """仅供启动入口和 HTTP 依赖组装使用，不传入业务服务。"""

    auth: PostgresClientManager
    identity: IdentityService
    meta: PostgresClientManager
    query: PostgresClientManager
    assistant: PostgresClientManager
    admin_doris: DorisClientManager
    query_clients: DorisQueryClientRegistry
    embedding: EmbeddingClient
    es: AsyncElasticsearch
    persistence: PostgresCheckpointStore
    locks: PostgresAdvisoryLocks
    sandbox: DockerSandboxManager
    agents: AgentManager
    runs: ConversationRunService
    conversations: ConversationLifecycleService
    user_deletion: UserDeletionService
    auth_rate_limit: AuthRateLimitService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时创建资源，失败及退出时逆序清理，避免应用实例之间共享连接。"""
    async with AsyncExitStack() as stack:
        resources = _create_resources(stack)
        logger.info("开始初始化应用资源")
        await resources.persistence.init()
        await resources.locks.init()
        await resources.sandbox.init()
        for postgres in (
            resources.auth,
            resources.meta,
            resources.query,
            resources.assistant,
        ):
            await postgres.init_tables()
        await resources.agents.init()
        logger.info("应用资源初始化完成")
        app.state.resources = resources
        try:
            yield
        finally:
            del app.state.resources


def _create_resources(stack: AsyncExitStack) -> WebResources:
    """逐项构造并登记当前 lifespan 的资源，联网准备由 lifespan 执行。"""
    auth = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    stack.push_async_callback(auth.close)
    meta = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    stack.push_async_callback(meta.close)
    query = PostgresClientManager(cfg.meta_postgresql, QueryBase)
    stack.push_async_callback(query.close)
    assistant = PostgresClientManager(cfg.langgraph_postgresql, AssistantBase)
    stack.push_async_callback(assistant.close)
    admin_doris = DorisClientManager(cfg.doris)
    stack.push_async_callback(admin_doris.close)
    query_clients = DorisQueryClientRegistry(cfg.doris)
    stack.push_async_callback(query_clients.close)
    embedding = EmbeddingClient(cfg.embedding)
    stack.push_async_callback(embedding.close)
    es = AsyncElasticsearch(
        hosts=[f"http://{cfg.elasticsearch.host}:{cfg.elasticsearch.port}"]
    )
    stack.push_async_callback(es.close)
    persistence = PostgresCheckpointStore(cfg.langgraph_postgresql)
    stack.push_async_callback(persistence.close)
    locks = PostgresAdvisoryLocks(cfg.langgraph_postgresql)
    stack.push_async_callback(locks.close)
    sandbox = DockerSandboxManager(
        cfg.sandbox, readonly_mounts=packaged_skill_readonly_mounts()
    )
    stack.push_async_callback(sandbox.close)
    tombstones = ConversationTombstoneStore(assistant)
    identity = IdentityService(auth, admin_doris)
    metadata_reader = MetadataReader(meta)
    recall = SemanticRecallService(
        identity,
        SemanticResourceService(meta, es, embedding),
        QueryExperienceService(query, metadata_reader, es, embedding),
        assistant,
    )
    factory = ConversationAgentRuntimeFactory(
        persistence,
        locks,
        sandbox,
        recall,
        QueryExecutionService(
            identity=identity,
            metadata=metadata_reader,
            postgres=query,
            query_clients=query_clients,
            artifact_store=sandbox,
        ),
    )
    agents = AgentManager(persistence, tombstones, locks, factory)
    stack.push_async_callback(agents.close)
    runs = ConversationRunService(agents, sandbox, locks)
    stack.push_async_callback(runs.close)
    conversations = build_conversation_lifecycle_service(
        locks,
        assistant,
        agents,
        sandbox,
        cfg.lifecycle,
        runs,
    )
    auth_rate_limit = AuthRateLimitService(
        redis_url=cfg.auth.rate_limit_redis_url.get_secret_value(),
    )
    stack.callback(auth_rate_limit.close)
    return WebResources(
        auth=auth,
        identity=identity,
        meta=meta,
        query=query,
        assistant=assistant,
        admin_doris=admin_doris,
        query_clients=query_clients,
        embedding=embedding,
        es=es,
        persistence=persistence,
        locks=locks,
        sandbox=sandbox,
        agents=agents,
        runs=runs,
        conversations=conversations,
        user_deletion=UserDeletionService(
            UserDeletionStateService(auth),
            sandbox,
            conversations,
        ),
        auth_rate_limit=auth_rate_limit,
    )
