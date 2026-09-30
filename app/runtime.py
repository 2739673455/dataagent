"""Web 进程资源所有权与启动装配；每次 lifespan 创建独立实例。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI
from loguru import logger

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.checkpoints.postgres import PostgresCheckpointStore
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.tombstones import ConversationTombstoneStore
from app.assistant.execution.manager import AgentManager
from app.assistant.execution.run import ConversationRunService
from app.assistant.execution.runtime_factory import ConversationAgentRuntimeFactory
from app.assistant.providers import build_conversation_lifecycle_service
from app.identity.services.rate_limit import AuthRateLimitService
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.metadata.services.recall_application import SemanticRecallService
from app.query.providers import build_query_execution_handler
from app.sandbox import DockerSandboxManager
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
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
    embedding: EmbeddingClient
    es: AsyncElasticsearch
    persistence: PostgresCheckpointStore
    locks: PostgresAdvisoryLocks
    sandbox: DockerSandboxManager
    agents: AgentManager
    runs: ConversationRunService
    conversations: ConversationLifecycleService
    user_deletion: UserDeletionService
    recall: SemanticRecallService
    auth_rate_limit: AuthRateLimitService


def _create_resources(stack: AsyncExitStack) -> WebResources:
    """逐项构造并登记当前 lifespan 的资源，联网准备由 lifespan 执行。"""
    auth = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    stack.push_async_callback(auth.close)
    meta = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    stack.push_async_callback(meta.close)
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
    recall = SemanticRecallService(auth, meta, embedding, es, admin_doris)
    factory = ConversationAgentRuntimeFactory(
        persistence,
        locks,
        sandbox,
        recall,
        build_query_execution_handler(sandbox, auth, meta, query_clients, admin_doris),
    )
    agents = AgentManager(persistence, tombstones, locks, factory)
    stack.push_async_callback(agents.close)
    runs = ConversationRunService(agents, sandbox, recall, locks)
    stack.push_async_callback(runs.close)
    conversations = build_conversation_lifecycle_service(
        locks,
        assistant,
        meta,
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
        meta=meta,
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
            PostgresUserDeletionStateStore(auth),
            sandbox,
            conversations,
        ),
        recall=recall,
        auth_rate_limit=auth_rate_limit,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时创建资源，失败及退出时逆序清理，避免应用实例之间共享连接。"""
    async with AsyncExitStack() as stack:
        resources = _create_resources(stack)
        logger.info("开始初始化应用资源")
        await resources.persistence.init()
        await resources.locks.init()
        await resources.sandbox.init()
        for postgres in (resources.auth, resources.meta, resources.assistant):
            await postgres.init_tables()
        await resources.agents.init()
        logger.info("应用资源初始化完成")
        app.state.resources = resources
        try:
            yield
        finally:
            del app.state.resources
