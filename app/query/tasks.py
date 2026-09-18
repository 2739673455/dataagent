"""查询经验索引后台任务。"""

from contextlib import AsyncExitStack
from uuid import UUID

from loguru import logger

from app.query.providers import build_query_experience_indexer
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.async_runtime import run_async
from app.shared.clients.embedding_client_manager import EmbeddingClientManager
from app.shared.clients.es_client_manager import ESClientManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg
from app.shared.database.base import MetaBase
from app.shared.tasks.celery_app import celery_app

_REPAIR_BATCH_SIZE = 500


async def _sync_index(experience_id: UUID, revision: int) -> int:
    """初始化任务资源并同步指定查询经验索引。"""
    embedding = EmbeddingClientManager(cfg.embedding)
    es = ESClientManager(cfg.elasticsearch)
    postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    async with AsyncExitStack() as stack:
        stack.push_async_callback(embedding.close)
        stack.push_async_callback(es.close)
        stack.push_async_callback(postgres.close)
        embedding.init()
        es.init()
        postgres.init()
        async with postgres.session() as session:
            return await build_query_experience_indexer(
                session,
                es.get_client(),
                embedding.get_client(),
            ).sync(experience_id, revision)


@celery_app.task(
    bind=True,
    name="dataagent.query.sync_index",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_index_task(
    self: object, experience_id: str, revision: int
) -> dict[str, object]:
    """同步一条查询经验索引并自动重试。"""
    del self
    logger.info(
        f"开始同步查询经验索引: experience_id={experience_id}, revision={revision}"
    )
    synced_revision = run_async(_sync_index(UUID(experience_id), revision))
    logger.info(
        "查询经验索引同步完成: "
        f"experience_id={experience_id}, revision={synced_revision}"
    )
    return {
        "experience_id": experience_id,
        "revision": synced_revision,
    }


async def _repair_indexes() -> dict[str, int]:
    """扫描索引版本落后的查询经验并提交补偿任务。"""
    postgres = PostgresClientManager(cfg.meta_postgresql, MetaBase)
    try:
        postgres.init()
        async with postgres.session() as session, session.begin():
            pending = await QueryExperiencePGRepo(session).list_pending_index_repairs(
                limit=_REPAIR_BATCH_SIZE
            )
        dispatched_count = sum(
            query_experience_index_scheduler.enqueue(experience_id, revision)
            for experience_id, revision in pending.items()
        )
        stats = {
            "attempted_count": len(pending),
            "dispatched_count": dispatched_count,
            "failed_count": len(pending) - dispatched_count,
        }
        logger.info(
            "查询经验索引补偿扫描完成: "
            f"attempted_count={stats['attempted_count']}, "
            f"dispatched_count={stats['dispatched_count']}, "
            f"failed_count={stats['failed_count']}"
        )
        return stats
    finally:
        await postgres.close()


@celery_app.task(name="dataagent.query.repair_indexes")
def repair_indexes_task() -> dict[str, int]:
    """提交一批待补偿的查询经验索引任务。"""
    return run_async(_repair_indexes())
