"""元数据脚本的资源生命周期与互斥执行。"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy import text

from app.metadata.providers import build_meta_index_service
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.import_service import MetaImportService
from app.metadata.services.index import MetaIndexService
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import MetaBase


@asynccontextmanager
async def metadata_import_services() -> AsyncGenerator[
    tuple[MetaImportService, MetaIndexService]
]:
    """串行执行全量和增量导入，退出时释放锁及全部客户端。"""
    postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    doris = DorisClientManager(cfg.doris)
    es = ESClientManager(cfg.elasticsearch)
    embedding = EmbeddingClientManager(cfg.embedding)
    async with AsyncExitStack() as stack:
        for manager in (postgres, doris, es, embedding):
            stack.push_async_callback(manager.close)
            manager.init()
        # 独立连接只持互斥锁；导入的数据读写使用另一个会话和短事务。
        lock_session = await stack.enter_async_context(postgres.session())
        await stack.enter_async_context(lock_session.begin())
        locked = await lock_session.scalar(
            text(
                "SELECT pg_try_advisory_xact_lock(hashtextextended('metadata-import', 0))"
            )
        )
        if not locked:
            raise RuntimeError("已有元数据导入脚本正在运行")
        await postgres.init_tables()
        session = await stack.enter_async_context(postgres.session())
        connection = await stack.enter_async_context(doris.connection())
        meta_repo = MetaPGRepo(session)
        source_repo = SourceDorisRepo(connection)
        index_service = build_meta_index_service(
            meta_repo, source_repo, es.get_client(), embedding.get_client()
        )
        yield MetaImportService(meta_repo, source_repo, index_service), index_service
