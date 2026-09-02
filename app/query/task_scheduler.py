"""查询经验索引 Celery 调度器。"""

from uuid import UUID

from loguru import logger

from app.shared.tasks.celery_app import celery_app


class CeleryQueryExperienceIndexScheduler:
    """通过 Celery 提交查询经验索引任务。"""

    def enqueue(self, experience_id: UUID, revision: int) -> bool:
        """提交指定经验版本的索引同步任务并返回是否成功。"""
        try:
            celery_app.send_task(
                "dataagent.query.sync_index",
                args=[str(experience_id), revision],
                queue="metadata-index",
                routing_key="metadata-index",
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "提交查询经验索引任务失败，等待定时补偿: "
                f"experience_id={experience_id}, revision={revision}"
            )
            return False


query_experience_index_scheduler = CeleryQueryExperienceIndexScheduler()
