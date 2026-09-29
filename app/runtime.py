"""Web 进程资源所有权与启动装配；每次 lifespan 创建独立实例。"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.assistant.agents.filesystem import packaged_skill_readonly_mounts
from app.assistant.providers import build_conversation_lifecycle_service
from app.assistant.services.lifecycle import ConversationLifecycleService
from app.assistant.services.manager import AgentManager
from app.assistant.services.run import ConversationRunService
from app.assistant.services.tombstones import ConversationTombstoneStore
from app.assistant.tasks import ConversationTasks
from app.metadata.services.recall_handler import SemanticRecallHandler
from app.query.providers import build_query_execution_handler
from app.sandbox.manager import DockerSandboxManager
from app.sandbox.providers import create_sandbox_manager
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import AssistantBase, AuthBase, MetaBase


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
    checkpoint_pool: AsyncConnectionPool[AsyncConnection[DictRow]]
    checkpointer: AsyncPostgresSaver
    sandbox: DockerSandboxManager
    agents: AgentManager
    runs: ConversationRunService
    conversations: ConversationLifecycleService
    recall: SemanticRecallHandler
    tasks: ConversationTasks


def _create_resources() -> WebResources:
    """组装当前 lifespan 的资源，联网初始化由 lifespan 执行。"""
    auth = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    meta = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    assistant = PostgresClientManager(cfg.langgraph_postgresql, AssistantBase)
    admin_doris = DorisClientManager(cfg.doris)
    query_clients = DorisQueryClientRegistry(cfg.doris)
    embedding = EmbeddingClientManager(cfg.embedding)
    es = ESClientManager(cfg.elasticsearch)
    db = cfg.langgraph_postgresql
    conninfo = make_conninfo(
        host=db.host,
        port=db.port,
        user=db.user,
        password=db.password.get_secret_value(),
        dbname=db.database,
    )
    checkpoint_pool = AsyncConnectionPool[AsyncConnection[DictRow]](
        conninfo=conninfo,
        min_size=1,
        max_size=20,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    checkpointer = AsyncPostgresSaver(checkpoint_pool)
    sandbox = create_sandbox_manager(packaged_skill_readonly_mounts())
    tombstones = ConversationTombstoneStore(assistant)
    recall = SemanticRecallHandler(auth, meta, embedding, es, admin_doris)
    agents = AgentManager(
        checkpointer,
        sandbox,
        tombstones,
        recall,
        build_query_execution_handler(sandbox, auth, query_clients),
    )
    runs = ConversationRunService(agents, sandbox)
    conversations = build_conversation_lifecycle_service(
        assistant,
        agents,
        sandbox,
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
        checkpoint_pool=checkpoint_pool,
        checkpointer=checkpointer,
        sandbox=sandbox,
        agents=agents,
        runs=runs,
        conversations=conversations,
        recall=recall,
        tasks=ConversationTasks(assistant, conversations),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时创建资源，失败及退出时逆序清理，避免应用实例之间共享连接。"""
    resources = _create_resources()
    async with AsyncExitStack() as stack:
        for resource in (
            resources.query_clients,
            resources.admin_doris,
            resources.auth,
            resources.meta,
            resources.assistant,
            resources.es,
            resources.embedding,
            resources.checkpoint_pool,
            resources.sandbox,
            resources.agents,
            resources.runs,
            resources.tasks,
        ):
            stack.push_async_callback(resource.close)
        logger.info("开始初始化应用资源")
        resources.embedding.init()
        resources.es.init()
        await resources.checkpoint_pool.open(wait=True)
        await resources.checkpointer.setup()
        await resources.sandbox.init()
        await resources.agents.init()
        for postgres in (resources.auth, resources.meta, resources.assistant):
            postgres.init()
            await postgres.init_tables()
        resources.admin_doris.init()
        logger.info("应用资源初始化完成")
        resources.tasks.start()
        app.state.resources = resources
        try:
            yield
        finally:
            del app.state.resources
