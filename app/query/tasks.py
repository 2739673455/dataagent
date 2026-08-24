"""查询经验索引后台任务"""

from uuid import UUID

from app.query.providers import build_query_experience_service
from app.query.task_scheduler import query_experience_index_scheduler
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import meta_postgres_client_manager
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.runner import run_async

_REPAIR_BATCH_SIZE = 500


async def _sync_index(experience_id: UUID, revision: int) -> int:
    embedding_client_manager.init()
    es_client_manager.init()
    meta_postgres_client_manager.init()
    try:
        async with meta_postgres_client_manager.session() as session:
            return await build_query_experience_service(session).sync_index(
                experience_id,
                revision,
            )
    finally:
        await meta_postgres_client_manager.close()
        await es_client_manager.close()
        await embedding_client_manager.close()


@celery_app.task(
    bind=True,
    name="dataagent.query.sync_index",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def sync_index_task(self: object, experience_id: str, revision: int) -> dict[str, object]:
    """同步一条查询经验索引并自动重试"""
    del self
    synced_revision = run_async(_sync_index(UUID(experience_id), revision))
    return {
        "experience_id": experience_id,
        "revision": synced_revision,
    }


async def _repair_indexes() -> int:
    embedding_client_manager.init()
    es_client_manager.init()
    meta_postgres_client_manager.init()
    try:
        async with meta_postgres_client_manager.session() as session:
            pending = await build_query_experience_service(
                session
            ).pending_index_repairs(limit=_REPAIR_BATCH_SIZE)
        for experience_id, revision in pending.items():
            query_experience_index_scheduler.enqueue(experience_id, revision)
        return len(pending)
    finally:
        await meta_postgres_client_manager.close()
        await es_client_manager.close()
        await embedding_client_manager.close()


@celery_app.task(name="dataagent.query.repair_indexes")
def repair_indexes_task() -> dict[str, int]:
    """提交一批待补偿的查询经验索引任务"""
    return {"dispatched_count": run_async(_repair_indexes())}
