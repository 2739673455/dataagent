"""脚本导入顺序、清理范围和水位提交边界回归。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.mysql import dialect

from app.metadata.errors import InvalidMetadataError
from app.metadata.models.catalog import ColumnInfo, ColumnMetric, MetricInfo, TableInfo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.import_service import MetaImportService, parse_metadata_yaml
from app.metadata.services.index import MetaIndexService
from scripts.import_metadata import run as import_file

VALID_YAML = b"""
tables:
  - name: orders
    role: fact
    description: Orders
    value_index_cursor_column: updated_at
    columns:
      - {name: status, description: Status, index_values: true}
metrics:
  - name: order_count
    description: Count
    relevant_columns: [{t_name: orders, c_name: status}]
"""


@asynccontextmanager
async def transaction():
    yield


def full_import_dependencies():
    events = []
    repo = MagicMock(session=MagicMock(begin=transaction))
    repo.replace_catalog = AsyncMock(side_effect=lambda *args: events.append("catalog"))
    source = MagicMock(
        table_exists=AsyncMock(return_value=True),
        get_primary_key_columns=AsyncMock(return_value=["id"]),
        get_column_types=AsyncMock(
            return_value={"id": "BIGINT", "status": "VARCHAR", "updated_at": "BIGINT"}
        ),
        get_table_columns_sample_values=AsyncMock(return_value={"status": ["paid"]}),
    )
    index = MagicMock()
    for name in (
        "reset_indexes",
        "build_column_indexes",
        "build_metric_indexes",
        "sync_column_values",
    ):
        setattr(
            index,
            name,
            AsyncMock(
                side_effect=lambda *args, step=name, **kwargs: events.append(step)
            ),
        )
    return repo, source, index, events


def test_full_import_runs_entire_pipeline_in_order():
    repo, source, index, events = full_import_dependencies()
    asyncio.run(
        MetaImportService(repo, source, index).import_full(
            parse_metadata_yaml(VALID_YAML)
        )
    )
    assert events == [
        "reset_indexes",
        "catalog",
        "build_column_indexes",
        "build_metric_indexes",
        "sync_column_values",
    ]
    tables, columns, metrics = repo.replace_catalog.call_args.args
    assert tables[0].primary_key_columns == ["id"]
    assert columns[0].examples == ["paid"]
    assert metrics[0].relevant_columns == [{"t_name": "orders", "c_name": "status"}]
    index.sync_column_values.assert_awaited_once_with(
        [("orders", "status")], mode="full"
    )


def test_invalid_source_preserves_existing_catalog_and_indexes():
    repo, source, index, events = full_import_dependencies()
    source.table_exists.return_value = False
    with pytest.raises(InvalidMetadataError):
        asyncio.run(
            MetaImportService(repo, source, index).import_full(
                parse_metadata_yaml(VALID_YAML)
            )
        )
    assert events == []


def test_index_failure_stops_full_pipeline():
    repo, source, index, _events = full_import_dependencies()
    index.build_column_indexes.side_effect = RuntimeError("embedding unavailable")
    with pytest.raises(RuntimeError, match="embedding"):
        asyncio.run(
            MetaImportService(repo, source, index).import_full(
                parse_metadata_yaml(VALID_YAML)
            )
        )
    index.build_metric_indexes.assert_not_awaited()
    index.sync_column_values.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        b"tables: []",
        VALID_YAML.replace(b"c_name: status", b"c_name: missing"),
        VALID_YAML.replace(b"role: fact", b"role: unknown"),
        VALID_YAML.replace(b"    columns:", b"    columns_unknown:"),
    ],
)
def test_bad_yaml_never_opens_import_resources(tmp_path, payload):
    path = tmp_path / "metadata.yaml"
    path.write_bytes(payload)
    with patch("scripts.import_metadata.metadata_import_services") as resources:
        with pytest.raises(InvalidMetadataError):
            asyncio.run(import_file(full=True, path=path))
        resources.assert_not_called()


def test_replace_catalog_clears_only_metadata_and_preserves_references():
    session = MagicMock(execute=AsyncMock(), flush=AsyncMock())
    table = TableInfo(
        name="orders", role="fact", description="Orders", primary_key_columns=["id"]
    )
    key = ColumnInfo(
        t_name="orders",
        name="id",
        type="BIGINT",
        description="ID",
        examples=[],
        alias=[],
        index_values=False,
    )
    column = ColumnInfo(
        t_name="orders",
        name="parent_id",
        type="BIGINT",
        description="Parent",
        examples=[],
        alias=[],
        index_values=False,
        reference_t_name="orders",
        reference_c_name="id",
    )
    metric = MetricInfo(
        name="count",
        description="Count",
        alias=[],
        relevant_columns=[{"t_name": "orders", "c_name": "id"}],
    )
    asyncio.run(MetaPGRepo(session).replace_catalog([table], [key, column], [metric]))
    statements = [
        str(call.args[0].compile(dialect=postgresql.dialect()))
        for call in session.execute.call_args_list
    ]
    assert statements == [
        f"DELETE FROM {name}"
        for name in (
            "semantic_recall_snapshots",
            "value_index_sync_state",
            "column_metric",
            "column_info",
            "metric_info",
            "table_info",
        )
    ]
    assert column.reference_t_name == "orders"
    assert column.reference_c_name == "id"
    relation = session.add_all.call_args.args[0][0]
    assert isinstance(relation, ColumnMetric)
    assert relation.metric_name == "count"


def incremental_dependencies(previous, upper):
    state = SimpleNamespace(
        current_generation=uuid4(),
        cursor_value=MetaIndexService._serialize_cursor(previous)
        if previous is not None
        else None,
        status="succeeded",
        active_run_id=None,
    )
    column = SimpleNamespace(index_values=True, value_index_state=state, meta_version=1)
    table = SimpleNamespace(value_index_cursor_column="updated_at", meta_version=1)
    repo = MagicMock(session=MagicMock(begin=transaction))
    repo.acquire_index_lock = AsyncMock()
    repo.get_column_info = AsyncMock(return_value=column)
    repo.get_table_info = AsyncMock(return_value=table)
    repo.reload_value_index_context = AsyncMock(return_value=(column, table))

    async def begin(*args, **kwargs):
        state.active_run_id = kwargs["run_id"]
        state.status = "syncing"

    async def complete(*args, **kwargs):
        state.cursor_value = kwargs["cursor_value"]
        state.status = "succeeded"
        state.active_run_id = None
        return True

    async def fail(*args, **kwargs):
        state.status = "failed"
        state.active_run_id = None
        return True

    repo.begin_value_index_sync = AsyncMock(side_effect=begin)
    repo.complete_value_index_sync = AsyncMock(side_effect=complete)
    repo.fail_value_index_sync = AsyncMock(side_effect=fail)

    async def batches(*args):
        yield ["paid", None, "cancelled"]

    source = MagicMock(
        get_value_sync_upper_bound=AsyncMock(return_value=upper),
        iter_changed_column_value_batches=MagicMock(side_effect=batches),
    )
    values = MagicMock(
        ensure_index=AsyncMock(), upsert=AsyncMock(), refresh=AsyncMock()
    )
    service = MetaIndexService(
        repo, source, MagicMock(), MagicMock(), MagicMock(), values
    )
    return service, repo, source, values, state


@pytest.mark.parametrize(
    "previous, upper", [(10, 10), (10, 9), (10, None), (None, None)]
)
def test_unchanged_or_lower_watermark_does_not_scan(previous, upper):
    service, _repo, source, values, state = incremental_dependencies(previous, upper)
    old = state.cursor_value
    asyncio.run(service.sync_column_values([("orders", "status")], mode="incremental"))
    source.iter_changed_column_value_batches.assert_not_called()
    values.upsert.assert_not_awaited()
    assert state.cursor_value == old


@pytest.mark.parametrize(
    "previous, upper",
    [
        (10, 20),
        (None, 20),
        (Decimal("1.5"), Decimal("2.5")),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)),
    ],
)
def test_new_watermark_advances_only_after_index_write(previous, upper):
    service, _repo, source, values, state = incremental_dependencies(previous, upper)
    result = asyncio.run(
        service.sync_column_values([("orders", "status")], mode="incremental")
    )
    source.iter_changed_column_value_batches.assert_called_once_with(
        "orders", "status", "updated_at", previous, upper
    )
    assert result[("orders", "status")].upserted_count == 2
    assert state.cursor_value == service._serialize_cursor(upper)
    values.refresh.assert_awaited_once()


def test_failed_index_write_does_not_advance_watermark_and_can_retry():
    service, repo, _source, values, state = incremental_dependencies(10, 20)
    values.upsert.side_effect = RuntimeError("ES unavailable")
    with pytest.raises(RuntimeError, match="ES unavailable"):
        asyncio.run(
            service.sync_column_values([("orders", "status")], mode="incremental")
        )
    assert state.cursor_value == service._serialize_cursor(10)
    assert state.status == "failed"
    repo.complete_value_index_sync.assert_not_awaited()
    values.upsert.side_effect = None
    asyncio.run(service.sync_column_values([("orders", "status")], mode="incremental"))
    assert state.cursor_value == service._serialize_cursor(20)


def test_each_table_upper_bound_is_loaded_once():
    service, _repo, source, _values, _state = incremental_dependencies(10, 20)
    asyncio.run(
        service.sync_column_values(
            [("orders", "status"), ("orders", "channel")], mode="incremental"
        )
    )
    source.get_value_sync_upper_bound.assert_awaited_once_with("orders", "updated_at")


@pytest.mark.parametrize("lower", [None, 10])
def test_doris_window_is_open_lower_closed_upper(lower):
    async def partitions(size):
        yield ["paid"]

    result = MagicMock(partitions=partitions)
    connection = MagicMock(
        dialect=dialect(), stream_scalars=AsyncMock(return_value=result)
    )

    async def read():
        return [
            batch
            async for batch in SourceDorisRepo(
                connection
            ).iter_changed_column_value_batches(
                "orders", "status", "updated_at", lower, 20
            )
        ]

    assert asyncio.run(read()) == [["paid"]]
    sql = str(connection.stream_scalars.call_args.args[0])
    assert "<= :upper_bound" in sql
    assert ("> :lower_bound" in sql) is (lower is not None)


def test_incremental_does_not_fall_back_to_full_import():
    service, _repo, source, values, state = incremental_dependencies(10, 20)
    state.current_generation = None
    with pytest.raises(RuntimeError, match="缺少全量同步状态"):
        asyncio.run(
            service.sync_column_values([("orders", "status")], mode="incremental")
        )
    source.get_value_sync_upper_bound.assert_not_awaited()
    values.upsert.assert_not_awaited()


def test_incremental_only_selects_enabled_columns_with_cursor():
    service, repo, _source, _values, _state = incremental_dependencies(10, 20)
    repo.list_table_infos = AsyncMock(
        return_value=[
            SimpleNamespace(name="orders", value_index_cursor_column="updated_at"),
            SimpleNamespace(name="static", value_index_cursor_column=None),
        ]
    )
    repo.list_column_infos = AsyncMock(
        return_value=[
            SimpleNamespace(t_name="orders", name="status", index_values=True),
            SimpleNamespace(t_name="orders", name="id", index_values=False),
            SimpleNamespace(t_name="static", name="name", index_values=True),
        ]
    )
    with patch.object(service, "sync_column_values", new=AsyncMock()) as sync:
        asyncio.run(service.import_incremental_values())
    sync.assert_awaited_once_with([("orders", "status")], mode="incremental")


def test_script_lock_conflict_closes_all_resources():
    from app.metadata.runtime import metadata_import_services

    session = MagicMock(begin=transaction, scalar=AsyncMock(return_value=False))

    @asynccontextmanager
    async def session_context():
        yield session

    postgres = MagicMock(
        session=session_context, close=AsyncMock(), init_tables=AsyncMock()
    )
    managers = [postgres, *[MagicMock(close=AsyncMock()) for _ in range(3)]]

    async def run():
        async with metadata_import_services():
            pytest.fail("must not enter import after lock conflict")

    with (
        patch("app.metadata.runtime.PostgresClientManager", return_value=managers[0]),
        patch("app.metadata.runtime.DorisClientManager", return_value=managers[1]),
        patch("app.metadata.runtime.ESClientManager", return_value=managers[2]),
        patch("app.metadata.runtime.EmbeddingClientManager", return_value=managers[3]),
        pytest.raises(RuntimeError, match="已有元数据导入脚本"),
    ):
        asyncio.run(run())
    postgres.init_tables.assert_not_awaited()
    for manager in managers:
        manager.close.assert_awaited_once()


def test_incremental_script_uses_saved_catalog_without_reading_yaml():
    importer = MagicMock(import_full=AsyncMock())
    indexer = MagicMock(import_incremental_values=AsyncMock())

    @asynccontextmanager
    async def services():
        yield importer, indexer

    with (
        patch("scripts.import_metadata.metadata_import_services", services),
        patch("scripts.import_metadata.parse_metadata_yaml") as parse,
    ):
        asyncio.run(import_file(full=False))
    parse.assert_not_called()
    importer.import_full.assert_not_awaited()
    indexer.import_incremental_values.assert_awaited_once()


@pytest.mark.parametrize(
    "args",
    [[], ["--full", "--incremental"]],
)
def test_import_cli_rejects_invalid_mode_arguments(args):
    from scripts.import_metadata import main

    with (
        patch("sys.argv", ["import_metadata", *args]),
        patch("scripts.import_metadata.run_async") as execute,
        pytest.raises(SystemExit) as error,
    ):
        main()
    assert error.value.code == 2
    execute.assert_not_called()


@pytest.mark.parametrize("kind", ["column", "metric"])
@pytest.mark.parametrize("write_fails", [False, True])
def test_semantic_build_embeds_unique_texts_and_marks_only_after_write(
    kind, write_fails
):
    item = SimpleNamespace(
        name="amount",
        description=" 金额 ",
        alias=["金额", "amount", "总额", " "],
        meta_version=1,
        t_name="orders",
        type="DECIMAL",
        examples=[],
        index_values=False,
        reference_t_name=None,
        reference_c_name=None,
        relevant_columns=[],
    )
    repo = MagicMock(session=MagicMock(begin=transaction))
    setattr(repo, f"get_{kind}_info", AsyncMock(return_value=item))
    mark = AsyncMock()
    setattr(repo, f"mark_{kind}_indexed", mark)

    async def write(documents):
        mark.assert_not_awaited()
        if write_fails:
            raise RuntimeError("write failed")

    index = MagicMock(write_documents=AsyncMock(side_effect=write))
    embedding = MagicMock(
        aembed_documents=AsyncMock(return_value=[[1.0], [2.0], [3.0]])
    )
    service = MetaIndexService(repo, MagicMock(), index, index, embedding, MagicMock())
    async def build():
        if kind == "column":
            await service.build_column_indexes([("orders", "amount")])
        else:
            await service.build_metric_indexes(["amount"])

    if write_fails:
        with pytest.raises(RuntimeError, match="write failed"):
            asyncio.run(build())
        mark.assert_not_awaited()
    else:
        asyncio.run(build())
        mark.assert_awaited_once()
    embedding.aembed_documents.assert_awaited_once_with(["amount", "总额", "金额"])
    documents = index.write_documents.call_args.args[0]
    assert [(doc.text, doc.text_type, doc.embedding) for doc in documents] == [
        ("amount", "name", [1.0]),
        ("总额", "alias", [2.0]),
        ("金额", "description", [3.0]),
    ]
