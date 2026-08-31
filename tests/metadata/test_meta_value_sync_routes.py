"""元数据取值索引手动同步接口测试。"""

import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.metadata.api.meta import schemas
from app.metadata.api.meta.router import sync_column_values, sync_table_values
from app.shared.tasks.submission import TaskSubmission


class MetaValueSyncRoutesTest(unittest.IsolatedAsyncioTestCase):
    """校验管理员选择的取值同步模式会传入后台任务。"""

    async def test_table_incremental_mode_is_submitted(self) -> None:
        """表取值增量同步模式应原样传给任务提交器。"""
        body = schemas.TableValueIndexSyncRequest(
            tables=["orders"],
            mode="incremental",
        )
        admin = MagicMock(id=7)

        with patch(
            "app.metadata.api.meta.router.enqueue_table_values",
            return_value=TaskSubmission(task_id="task-table"),
        ) as enqueue:
            response = await sync_table_values(body, admin)

        self.assertEqual(response.task_id, "task-table")
        enqueue.assert_called_once_with(["orders"], mode="incremental")

    async def test_column_full_mode_is_submitted(self) -> None:
        """字段取值全量同步模式应原样传给任务提交器。"""
        body = schemas.ColumnValueIndexSyncRequest(
            columns=[schemas.ColumnReference(t_name="orders", c_name="status")],
            mode="full",
        )
        admin = MagicMock(id=7)

        with patch(
            "app.metadata.api.meta.router.enqueue_column_values",
            return_value=TaskSubmission(task_id="task-column"),
        ) as enqueue:
            response = await sync_column_values(body, admin)

        self.assertEqual(response.task_id, "task-column")
        enqueue.assert_called_once_with([("orders", "status")], mode="full")

    def test_manual_value_sync_mode_is_required(self) -> None:
        """手动取值同步请求必须明确指定同步模式。"""
        with self.assertRaises(ValidationError):
            schemas.TableValueIndexSyncRequest.model_validate({"tables": ["orders"]})


if __name__ == "__main__":
    unittest.main()
