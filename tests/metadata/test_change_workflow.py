"""元数据提交与跨领域后续操作的顺序、预检和失败边界。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metadata.catalog.importer import ImportMode, MetaImportService
from app.metadata.catalog.service import MetaCatalogService
from app.metadata.config import ColumnConfig, MetaConfig, MetricConfig, TableConfig
from app.metadata.models.catalog import ColumnInfo, MetricInfo, TableInfo
from app.metadata.repositories.postgres import MetaPGRepo
from app.shared.tasks.submission import TaskSubmission
from app.workflows.metadata_changes import MetadataChangeWorkflow


def _dependencies(*, fail_commit=False, fail_invalidation=False, fail_submission=False):
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

    async def invalidate(**kwargs):
        assert events[-1] == "commit"
        events.append("invalidate")
        if fail_invalidation:
            raise RuntimeError("invalidate")

    def enqueue(keys):
        assert events[-1] in ("commit", "invalidate")
        events.append("enqueue")
        if fail_submission:
            raise RuntimeError("broker")
        return TaskSubmission(task_id="columns")

    invalidator = MagicMock(invalidate_assets=AsyncMock(side_effect=invalidate))
    scheduler = MagicMock(enqueue_columns=MagicMock(side_effect=enqueue))
    workflow = MetadataChangeWorkflow(invalidator, scheduler)
    return repo, source, indexes, invalidator, scheduler, workflow, events


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, ["commit", "invalidate", "enqueue"]),
        ("commit", []),
        ("invalidate", ["commit", "invalidate"]),
        ("broker", ["commit", "invalidate", "enqueue"]),
    ],
)
def test_column_change_commits_before_invalidating_and_submitting(failure, expected):
    repo, source, indexes, _, _, workflow, events = _dependencies(
        fail_commit=failure == "commit",
        fail_invalidation=failure == "invalidate",
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


def test_unchanged_column_does_not_invalidate_or_submit():
    repo, source, indexes, invalidator, scheduler, workflow, _ = _dependencies()
    repo.upsert_column_info.return_value = False
    result = asyncio.run(
        MetaCatalogService(repo, source, indexes, workflow).upsert_column_info(
            "orders", "id", "订单编号", [], False
        )
    )
    assert result is None
    invalidator.invalidate_assets.assert_not_awaited()
    scheduler.enqueue_columns.assert_not_called()


@pytest.mark.parametrize("resource", ["column", "metric"])
@pytest.mark.parametrize(
    "aliases,changed",
    [(["B", "A"], False), (["A", "C"], True), (["A"], True), (["A", "B", "C"], True)],
)
def test_alias_comparison_controls_versions_and_catalog_tasks(
    resource, aliases, changed
):
    repo, source, indexes, invalidator, scheduler, workflow, _ = _dependencies()
    if resource == "column":
        existing = ColumnInfo(
            t_name="orders",
            name="id",
            type="BIGINT",
            description="编号",
            examples=[1],
            alias=["A", "B"],
            index_values=False,
            reference_t_name=None,
            reference_c_name=None,
            meta_version=4,
            index_version=3,
        )
    else:
        existing = MetricInfo(
            name="count",
            description="数量",
            alias=["A", "B"],
            meta_version=4,
            index_version=3,
        )
    session = MagicMock(
        get=AsyncMock(return_value=existing),
        merge=AsyncMock(),
        scalars=AsyncMock(return_value=[]),
        execute=AsyncMock(),
    )
    real_repo = MetaPGRepo(session)
    service = MetaCatalogService(repo, source, indexes, workflow)
    if resource == "column":
        repo.upsert_column_info.side_effect = real_repo.upsert_column_info
        asyncio.run(service.upsert_column_info("orders", "id", "编号", aliases, False))
        enqueue = scheduler.enqueue_columns
    else:
        repo.upsert_metric_info = AsyncMock(side_effect=real_repo.upsert_metric_info)
        asyncio.run(
            service.upsert_metric_info(
                MetricInfo(name="count", description="数量", alias=aliases)
            )
        )
        enqueue = scheduler.enqueue_metrics
    written = session.merge.call_args.args[0]
    assert written.meta_version == 4 + int(changed)
    assert written.index_version == 3
    assert written.alias == aliases
    assert existing.alias == ["A", "B"]
    assert enqueue.call_count == int(changed)
    assert invalidator.invalidate_assets.await_count == int(
        changed and resource == "column"
    )


@pytest.mark.parametrize("mode", [ImportMode.MERGE, ImportMode.REPLACE])
def test_import_alias_reordering_does_not_write_or_schedule(mode):
    repo, source, indexes, invalidator, scheduler, workflow, _ = _dependencies()
    repo.list_table_infos.return_value = [
        TableInfo(
            name="orders",
            role="fact",
            description="订单",
            primary_key_columns=["id"],
            value_index_cursor_column=None,
        )
    ]
    repo.list_column_infos.return_value = [
        ColumnInfo(
            t_name="orders",
            name="id",
            type="BIGINT",
            description="编号",
            examples=[1],
            alias=["A", "B"],
            index_values=False,
            reference_t_name=None,
            reference_c_name=None,
        )
    ]
    repo.list_metric_infos.return_value = [
        MetricInfo(name="count", description="数量", alias=["A", "B"])
    ]
    config = MetaConfig(
        tables=[
            TableConfig(
                name="orders",
                role="fact",
                description="订单",
                columns=[
                    ColumnConfig(
                        name="id",
                        description="编号",
                        alias=["B", "A"],
                        index_values=False,
                    )
                ],
            )
        ],
        metrics=[MetricConfig(name="count", description="数量", alias=["B", "A"])],
    )
    result = asyncio.run(
        MetaImportService(repo, source, indexes, workflow).import_metadata(
            config, mode, False
        )
    )
    assert result.columns.updated == []
    assert result.metrics.updated == []
    repo.upsert_column_infos.assert_awaited_once_with(
        [], force_version_increment_keys=set()
    )
    repo.upsert_metric_info.assert_not_called()
    invalidator.invalidate_assets.assert_not_awaited()
    scheduler.enqueue_columns.assert_not_called()
    scheduler.enqueue_metrics.assert_not_called()


@pytest.mark.parametrize("entry", ["import", "catalog"])
def test_column_write_boundaries_normalize_examples(entry):
    repo, source, indexes, _, _, workflow, _ = _dependencies()
    values = [Decimal("2.5"), date(2026, 9, 27), 1]
    source.get_column_values.return_value = values
    source.get_table_columns_sample_values.return_value = {"id": values}
    if entry == "catalog":
        asyncio.run(
            MetaCatalogService(repo, source, indexes, workflow).upsert_column_info(
                "orders", "id", "编号", [], False
            )
        )
        column = repo.upsert_column_info.call_args.args[0]
    else:
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
        asyncio.run(
            MetaImportService(repo, source, indexes, workflow).import_metadata(
                config, ImportMode.MERGE, False
            )
        )
        column = repo.upsert_column_infos.call_args.args[0][0]
    assert column.examples == [1, 2.5, "2026-09-27"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_replace_import_invalidates_deleted_assets_but_only_indexes_new_ones(dry_run):
    repo, source, indexes, invalidator, scheduler, workflow, events = _dependencies()
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
        invalidator.invalidate_assets.assert_not_awaited()
        scheduler.enqueue_columns.assert_not_called()
        assert events == ["commit"]  # 只有读取现状的事务。
    else:
        invalidator.invalidate_assets.assert_awaited_once_with(
            table_names={"old_orders"}, column_keys={("old_orders", "id")}
        )
        scheduler.enqueue_columns.assert_called_once_with([("orders", "id")])
        scheduler.enqueue_metrics.assert_not_called()
        assert events == ["commit", "commit", "invalidate", "enqueue"]
