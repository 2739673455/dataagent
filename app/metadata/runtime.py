"""元数据脚本的资源生命周期与互斥执行。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from elasticsearch import AsyncElasticsearch
from loguru import logger
from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import RedisError

from app.metadata.providers import build_meta_index_service
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.index import MetaIndexService
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import MetaBase

_LOCK_TIMEOUT_SECONDS = 60
_LOCK_RENEW_SECONDS = 20


@asynccontextmanager
async def metadata_import_service() -> AsyncGenerator[MetaIndexService]:
    """通过 Redis 租约串行执行全量和增量导入。"""
    async with Redis.from_url(
        cfg.metadata.redis_url.get_secret_value(),
        socket_connect_timeout=5,
        socket_timeout=5,
    ) as redis:
        lock = redis.lock(
            f"metadata-import:{cfg.meta_postgresql.host}:"
            f"{cfg.meta_postgresql.port}:{cfg.meta_postgresql.database}",
            timeout=_LOCK_TIMEOUT_SECONDS,
            blocking=False,
            thread_local=False,
        )
        if not await lock.acquire():
            raise RuntimeError("已有元数据导入脚本正在运行")
        try:
            async with asyncio.TaskGroup() as group:
                renewal = group.create_task(_renew_import_lock(lock))
                try:
                    async with AsyncExitStack() as stack:
                        postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
                        stack.push_async_callback(postgres.close)
                        doris = DorisClientManager(cfg.doris)
                        stack.push_async_callback(doris.close)
                        es = AsyncElasticsearch(
                            hosts=[
                                f"http://{cfg.elasticsearch.host}:{cfg.elasticsearch.port}"
                            ]
                        )
                        stack.push_async_callback(es.close)
                        embedding = EmbeddingClient(cfg.embedding)
                        stack.push_async_callback(embedding.close)
                        await postgres.init_tables()
                        session = await stack.enter_async_context(postgres.session())
                        connection = await stack.enter_async_context(
                            doris.engine.connect()
                        )
                        service = build_meta_index_service(
                            MetaPGRepo(session),
                            SourceDorisRepo(connection),
                            es,
                            embedding,
                        )
                        yield service
                finally:
                    renewal.cancel()
        finally:
            try:
                # redis-py 按持有者 token 释放，租约过期后不会删除其他任务的锁。
                await lock.release()
            except RedisError:
                logger.exception("元数据导入锁释放失败，剩余租约将自动过期")


async def _renew_import_lock(lock: Lock) -> None:
    """定期续租；续租失败由 TaskGroup 取消正在执行的导入。"""
    while True:
        await asyncio.sleep(_LOCK_RENEW_SECONDS)
        await lock.extend(_LOCK_TIMEOUT_SECONDS, replace_ttl=True)
