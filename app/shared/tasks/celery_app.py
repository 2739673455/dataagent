"""Celery 应用与队列配置。"""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.shared.config.app_config import cfg

TASK_VISIBILITY_TIMEOUT_SECONDS = cfg.task_queue.task_time_limit_seconds + 300

celery_app = Celery(
    "dataagent",
    broker=cfg.task_queue.broker_url.get_secret_value(),
    backend=cfg.task_queue.result_backend.get_secret_value(),
    include=[
        "app.assistant.tasks",
        "app.metadata.tasks",
        "app.query.tasks",
        "app.workflows.tasks",
    ],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": TASK_VISIBILITY_TIMEOUT_SECONDS,
    },
    enable_utc=True,
    result_accept_content=["json"],
    result_expires=cfg.task_queue.result_expires_seconds,
    result_serializer="json",
    task_acks_late=True,
    task_create_missing_queues=False,
    task_default_exchange="dataagent",
    task_default_exchange_type="direct",
    task_default_queue="default",
    task_default_routing_key="default",
    task_queues=(
        Queue("default", routing_key="default"),
        Queue("metadata-index", routing_key="metadata-index"),
        Queue("lifecycle", routing_key="lifecycle"),
        Queue("lightweight", routing_key="lightweight"),
    ),
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
    task_reject_on_worker_lost=True,
    task_routes={
        "dataagent.assistant.generate_conversation_title": {
            "queue": "lightweight",
            "routing_key": "lightweight",
        },
        "dataagent.assistant.*": {
            "queue": "lifecycle",
            "routing_key": "lifecycle",
        },
        "dataagent.metadata.*": {
            "queue": "metadata-index",
            "routing_key": "metadata-index",
        },
        "dataagent.query.*": {
            "queue": "metadata-index",
            "routing_key": "metadata-index",
        },
        "dataagent.workflows.*": {
            "queue": "lifecycle",
            "routing_key": "lifecycle",
        },
    },
    task_serializer="json",
    task_soft_time_limit=cfg.task_queue.task_soft_time_limit_seconds,
    task_time_limit=cfg.task_queue.task_time_limit_seconds,
    task_track_started=True,
    timezone="Asia/Shanghai",
    worker_prefetch_multiplier=cfg.task_queue.worker_prefetch_multiplier,
)

celery_app.conf.beat_schedule = {
    "value-index-daily-dispatch": {
        "task": "dataagent.metadata.dispatch_value_indexes",
        "schedule": crontab(
            hour=cfg.task_queue.value_index_sync_time.hour,
            minute=cfg.task_queue.value_index_sync_time.minute,
        ),
    },
    "lifecycle-periodic-dispatch": {
        "task": "dataagent.assistant.cleanup_expired_drafts",
        "schedule": cfg.task_queue.lifecycle_schedule_seconds,
    },
    "conversation-title-repair": {
        "task": "dataagent.assistant.repair_conversation_titles",
        "schedule": cfg.task_queue.lifecycle_schedule_seconds,
    },
    "user-deletion-recovery": {
        "task": "dataagent.workflows.dispatch_due_user_deletions",
        "schedule": cfg.lifecycle.user_deletion_retry_seconds,
    },
    "query-experience-index-repair": {
        "task": "dataagent.query.repair_indexes",
        "schedule": cfg.task_queue.query_experience_repair_seconds,
    },
}
