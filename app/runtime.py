"""Web 进程资源所有权与启动装配；每次 lifespan 创建独立实例。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI
from loguru import logger

from app.assistant import (
    ConversationLifecycleService,
    SemanticRecallService,
)
from app.assistant.agents.filesystem import analyst_skill_mount
from app.assistant.agents.runtime import ConversationAgentRuntimeFactory
from app.assistant.execution.activity import SessionActivity
from app.assistant.execution.runs import ConversationRunService
from app.assistant.execution.runtime_cache import AgentManager
from app.assistant.models.base import AssistantBase
from app.assistant.repositories.checkpoint import PostgresCheckpointStore
from app.assistant.repositories.conversation_tombstone import ConversationTombstoneStore
from app.assistant.sessions.state_reader import AgentStateReader
from app.identity import IdentityService, UserDeletionStateService
from app.identity.auth.rate_limit import AuthRateLimitService
from app.identity.models.base import AuthBase
from app.metadata import MetadataReader, SemanticResourceService
from app.metadata.models.base import MetaBase
from app.query import (
    QueryExecutionService,
    QueryExperienceService,
    query_experience_index_scheduler,
)
from app.query.models.base import QueryBase
from app.sandbox import DockerSandboxManager
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.workflows import UserDeletionService


@dataclass(frozen=True, slots=True)
class WebResources:
    """启动入口和 HTTP 依赖组装所需的应用资源集合。"""

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
    agent_state: AgentStateReader
    runs: ConversationRunService
    conversations: ConversationLifecycleService
    user_deletion: UserDeletionService
    auth_rate_limit: AuthRateLimitService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """为当前应用实例创建资源，并在启动失败或退出时逆序释放。"""
    async with AsyncExitStack() as stack:
        resources = _create_resources(stack)
        logger.info("开始初始化应用资源")
        await resources.persistence.init()
        await resources.locks.init()
        await resources.sandbox.init()
        for postgres, metadata in (
            (resources.auth, AuthBase.metadata),
            (resources.meta, MetaBase.metadata),
            (resources.query, QueryBase.metadata),
            (resources.assistant, AssistantBase.metadata),
        ):
            await postgres.init_tables(metadata)
        await resources.agents.init()
        logger.info("应用资源初始化完成")
        app.state.resources = resources
        try:
            yield
        finally:
            del app.state.resources


def _create_resources(stack: AsyncExitStack) -> WebResources:
    """逐项构造并登记当前 lifespan 的资源，联网准备由 lifespan 执行。"""
    auth = PostgresClientManager(cfg.auth_postgresql)
    stack.push_async_callback(auth.close)
    meta = PostgresClientManager(cfg.meta_postgresql)
    stack.push_async_callback(meta.close)
    query = PostgresClientManager(cfg.meta_postgresql)
    stack.push_async_callback(query.close)
    assistant = PostgresClientManager(cfg.langgraph_postgresql)
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
        cfg.sandbox, readonly_mounts=(analyst_skill_mount(),)
    )
    stack.push_async_callback(sandbox.close)
    tombstones = ConversationTombstoneStore(assistant)
    identity = IdentityService(auth, admin_doris)
    metadata_reader = MetadataReader(meta)
    recall = SemanticRecallService(
        identity,
        SemanticResourceService(
            meta,
            es,
            embedding,
            data_source=cfg.query.data_source,
            database_name=cfg.doris.database,
        ),
        QueryExperienceService(
            query,
            metadata_reader,
            es,
            embedding,
            config=cfg.query,
            database_name=cfg.doris.database,
            index_scheduler=query_experience_index_scheduler,
        ),
        assistant,
    )
    activity = SessionActivity()
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
            config=cfg.query,
            database_name=cfg.doris.database,
            index_scheduler=query_experience_index_scheduler,
        ),
        activity,
    )
    agents = AgentManager(persistence, tombstones, locks, factory)
    stack.push_async_callback(agents.close)
    agent_state = AgentStateReader(persistence, tombstones, activity)
    runs = ConversationRunService(agents, sandbox, locks)
    stack.push_async_callback(runs.close)
    conversations = ConversationLifecycleService(
        assistant,
        locks,
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
        agent_state=agent_state,
        runs=runs,
        conversations=conversations,
        user_deletion=UserDeletionService(
            UserDeletionStateService(auth),
            sandbox,
            conversations,
        ),
        auth_rate_limit=auth_rate_limit,
    )
