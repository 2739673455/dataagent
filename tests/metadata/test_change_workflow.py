"""元数据提交与跨领域后续操作的顺序、预检和失败边界。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.config import ColumnConfig, MetaConfig, TableConfig
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.services.catalog import MetaCatalogService
from app.metadata.services.import_service import ImportMode, MetaImportService
from app.shared.tasks.submission import TaskSubmission
from app.workflows.metadata_changes import MetadataChangeWorkflow


def _dependencies(*, fail_commit=False, fail_submission=False):
    events = []

    @asynccontextmanager
    async def transaction():
        yield
        if fail_commit:
            raise RuntimeError("commit")
        events.append("commit")

    repo = MagicMock()
    repo.session.begin.side_effect = transaction
    repo.get_table_info = AsyncMock()
    repo.upsert_column_info = AsyncMock(return_value=True)
    repo.upsert_table_info = AsyncMock()
    repo.upsert_column_infos = AsyncMock()
    repo.list_table_infos = AsyncMock(return_value=[])
    repo.list_column_infos = AsyncMock(return_value=[])
    repo.list_metric_infos = AsyncMock(return_value=[])
    for name in ("delete_table_infos", "delete_column_infos", "delete_metric_infos"):
        setattr(repo, name, AsyncMock())
    source = MagicMock(
        table_exists=AsyncMock(return_value=True),
        get_primary_key_columns=AsyncMock(return_value=["id"]),
        get_column_types=AsyncMock(return_value={"id": "BIGINT"}),
        get_column_values=AsyncMock(return_value=[1]),
        get_table_columns_sample_values=AsyncMock(return_value={"id": [1]}),
    )
    indexes = MagicMock(
        delete_column_indexes=AsyncMock(), delete_metric_indexes=AsyncMock()
    )

    def enqueue(keys):
        assert events[-1] == "commit"
        events.append("enqueue")
        if fail_submission:
            raise RuntimeError("broker")
        return TaskSubmission(task_id="columns")

    scheduler = MagicMock(enqueue_columns=MagicMock(side_effect=enqueue))
    workflow = MetadataChangeWorkflow(scheduler)
    return repo, source, indexes, scheduler, workflow, events


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, ["commit", "enqueue"]),
        ("commit", []),
        ("broker", ["commit", "enqueue"]),
    ],
)
def test_column_change_commits_before_submitting(failure, expected):
    repo, source, indexes, _, workflow, events = _dependencies(
        fail_commit=failure == "commit",
        fail_submission=failure == "broker",
    )
    service = MetaCatalogService(repo, source, indexes, workflow)

    async def run():
        return await service.upsert_column_info("orders", "id", "订单编号", [], False)

    if failure:
        with pytest.raises(RuntimeError, match=failure):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == TaskSubmission(task_id="columns")
    assert events == expected


def test_unchanged_column_does_not_submit():
    repo, source, indexes, scheduler, workflow, _ = _dependencies()
    repo.upsert_column_info.return_value = False
    result = asyncio.run(
        MetaCatalogService(repo, source, indexes, workflow).upsert_column_info(
            "orders", "id", "订单编号", [], False
        )
    )
    assert result is None
    scheduler.enqueue_columns.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False])
def test_replace_import_removes_old_indexes_and_indexes_new_columns(dry_run):
    repo, source, indexes, scheduler, workflow, events = _dependencies()
    repo.list_table_infos.return_value = [
        TableInfo(
            name="old_orders",
            role="fact",
            primary_key_columns=["id"],
            description="旧表",
        )
    ]
    repo.list_column_infos.return_value = [
        ColumnInfo(
            t_name="old_orders",
            name="id",
            type="BIGINT",
            description="编号",
            examples=[1],
            alias=[],
            index_values=False,
        )
    ]
    config = MetaConfig(
        tables=[
            TableConfig(
                name="orders",
                role="fact",
                description="订单",
                columns=[
                    ColumnConfig(name="id", description="编号", index_values=False)
                ],
            )
        ]
    )
    result = asyncio.run(
        MetaImportService(repo, source, indexes, workflow).import_metadata(
            config, ImportMode.REPLACE, dry_run
        )
    )
    assert result.columns.deleted == [("old_orders", "id")]
    assert result.columns.created == [("orders", "id")]
    if dry_run:
        repo.upsert_column_infos.assert_not_awaited()
        indexes.delete_column_indexes.assert_not_awaited()
        scheduler.enqueue_columns.assert_not_called()
        assert events == ["commit"]  # 只有读取现状的事务。
    else:
        indexes.delete_column_indexes.assert_awaited_once_with([("old_orders", "id")])
        scheduler.enqueue_columns.assert_called_once_with([("orders", "id")])
        scheduler.enqueue_metrics.assert_not_called()
        assert events == ["commit", "commit", "enqueue"]
