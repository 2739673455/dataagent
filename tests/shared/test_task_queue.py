"""Celery 队列和周期任务配置测试"""

import unittest

from app.shared.config.app_config import cfg
from app.shared.tasks.celery_app import celery_app


class TaskQueueConfigTest(unittest.TestCase):
    def test_declares_all_routed_queues(self) -> None:
        queue_names = {queue.name for queue in celery_app.conf.task_queues}
        self.assertEqual(
            queue_names,
            {"default", "metadata-index", "lifecycle", "lightweight"},
        )

    def test_registers_periodic_repair_tasks(self) -> None:
        scheduled_tasks = {
            entry["task"] for entry in celery_app.conf.beat_schedule.values()
        }
        self.assertEqual(
            scheduled_tasks,
            {
                "dataagent.metadata.dispatch_value_indexes",
                "dataagent.analytics.cleanup_expired_drafts",
                "dataagent.analytics.repair_conversation_titles",
                "dataagent.workflows.dispatch_due_user_deletions",
                "dataagent.query.repair_indexes",
            },
        )

    def test_schedules_value_index_increment_at_configured_daily_time(self) -> None:
        schedule = celery_app.conf.beat_schedule["value-index-daily-dispatch"][
            "schedule"
        ]

        self.assertEqual(schedule.hour, {cfg.task_queue.value_index_sync_time.hour})
        self.assertEqual(
            schedule.minute,
            {cfg.task_queue.value_index_sync_time.minute},
        )

    def test_imports_all_business_task_modules(self) -> None:
        celery_app.loader.import_default_modules()
        registered = {
            name for name in celery_app.tasks if name.startswith("dataagent.")
        }
        self.assertTrue(
            {
                "dataagent.metadata.sync_column_indexes",
                "dataagent.query.sync_index",
                "dataagent.analytics.generate_conversation_title",
                "dataagent.analytics.delete_conversation_resources",
                "dataagent.workflows.delete_user",
            }.issubset(registered)
        )


if __name__ == "__main__":
    unittest.main()
