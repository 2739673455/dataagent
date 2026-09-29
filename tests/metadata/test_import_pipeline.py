"""脚本导入顺序、清理范围和水位提交边界回归。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.mysql import dialect

from app.metadata.errors import InvalidMetadataError
from app.metadata.models.catalog import (
    ColumnInfo,
    ColumnMetric,
    MetricInfo,
    TableInfo,
    column_resource_key,
)
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.services.index import MetaIndexService, parse_metadata_yaml
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
    indexes = [
        MagicMock(
            reset_index=AsyncMock(side_effect=lambda step=name: events.append(step))
        )
        for name in ("reset_columns", "reset_metrics", "reset_values")
    ]
    index = MetaIndexService(
        repo, source, indexes[0], indexes[1], MagicMock(), indexes[2]
    )
    for name in (
        "_build_column_indexes",
        "_build_metric_indexes",
        "_sync_column_values",
    ):
        setattr(
            index,
            name,
            AsyncMock(
                side_effect=lambda *args, step=name, **kwargs: events.append(step)
            ),
        )
    return repo, source, cast(Any, index), events


def test_full_import_runs_entire_pipeline_in_order():
    repo, _source, index, events = full_import_dependencies()
    asyncio.run(index.import_full(parse_metadata_yaml(VALID_YAML)))
    assert events == [
        "reset_columns",
        "reset_metrics",
        "reset_values",
        "catalog",
        "_build_column_indexes",
        "_build_metric_indexes",
        "_sync_column_values",
    ]
    tables, columns, metrics = repo.replace_catalog.call_args.args
    index._build_column_indexes.assert_awaited_once_with(columns)
    index._build_metric_indexes.assert_awaited_once_with(metrics)
    assert tables[0].primary_key_columns == ["id"]
    assert columns[0].examples == ["paid"]
    assert metrics[0].relevant_columns == [{"t_name": "orders", "c_name": "status"}]
    index._sync_column_values.assert_awaited_once_with(
        [("orders", "status")], mode="full"
    )


def test_invalid_source_preserves_existing_catalog_and_indexes():
    _repo, source, index, events = full_import_dependencies()
    source.table_exists.return_value = False
    with pytest.raises(InvalidMetadataError):
        asyncio.run(index.import_full(parse_metadata_yaml(VALID_YAML)))
    assert events == []


def test_index_failure_stops_full_pipeline():
    _repo, _source, index, _events = full_import_dependencies()
    index._build_column_indexes.side_effect = RuntimeError("embedding unavailable")
    with pytest.raises(RuntimeError, match="embedding"):
        asyncio.run(index.import_full(parse_metadata_yaml(VALID_YAML)))
    index._build_metric_indexes.assert_not_awaited()
    index._sync_column_values.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        VALID_YAML.replace(b"c_name: status", b"c_name: missing"),
        VALID_YAML.replace(b"role: fact", b"role: unknown"),
        VALID_YAML.replace(b"    columns:", b"    columns_unknown:"),
    ],
)
def test_bad_yaml_never_opens_import_resources(tmp_path, payload):
    path = tmp_path / "metadata.yaml"
    path.write_bytes(payload)
    with patch("scripts.import_metadata.metadata_import_service") as resources:
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
    column = SimpleNamespace(
        index_values=True,
        value_index_cursor_value=MetaIndexService._serialize_cursor(previous)
        if previous is not None
        else None,
    )
    table = SimpleNamespace(value_index_cursor_column="updated_at")
    repo = MagicMock(session=MagicMock(begin=transaction))
    repo.get_column_info = AsyncMock(return_value=column)
    repo.get_table_info = AsyncMock(return_value=table)

    async def update_cursor(t_name, c_name, cursor_value):
        column.value_index_cursor_value = cursor_value

    repo.update_value_index_cursor = AsyncMock(side_effect=update_cursor)

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
    return service, repo, source, values, column


@pytest.mark.parametrize(
    "previous, upper", [(10, 10), (10, 9), (10, None), (None, None)]
)
def test_unchanged_or_lower_watermark_does_not_scan(previous, upper):
    service, repo, source, values, state = incremental_dependencies(previous, upper)
    old = state.value_index_cursor_value
    asyncio.run(service._sync_column_values([("orders", "status")], mode="incremental"))
    source.iter_changed_column_value_batches.assert_not_called()
    values.upsert.assert_not_awaited()
    assert state.value_index_cursor_value == old
    repo.update_value_index_cursor.assert_not_awaited()


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
    asyncio.run(service._sync_column_values([("orders", "status")], mode="incremental"))
    source.iter_changed_column_value_batches.assert_called_once_with(
        "orders", "status", "updated_at", previous, upper
    )
    assert [item.value for item in values.upsert.call_args.args[0]] == [
        "paid",
        "cancelled",
    ]
    assert state.value_index_cursor_value == service._serialize_cursor(upper)
    values.refresh.assert_awaited_once()


def test_failed_index_write_does_not_advance_watermark_and_can_retry():
    service, repo, _source, values, state = incremental_dependencies(10, 20)
    values.upsert.side_effect = RuntimeError("ES unavailable")
    with pytest.raises(RuntimeError, match="ES unavailable"):
        asyncio.run(
            service._sync_column_values([("orders", "status")], mode="incremental")
        )
    assert state.value_index_cursor_value == service._serialize_cursor(10)
    repo.update_value_index_cursor.assert_not_awaited()
    values.upsert.side_effect = None
    asyncio.run(service._sync_column_values([("orders", "status")], mode="incremental"))
    assert state.value_index_cursor_value == service._serialize_cursor(20)


def test_each_table_upper_bound_is_loaded_once():
    service, _repo, source, _values, _state = incremental_dependencies(10, 20)
    asyncio.run(
        service._sync_column_values(
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


@pytest.mark.parametrize("upper", [None, 20])
def test_first_incremental_starts_without_watermark(upper):
    service, _repo, source, values, state = incremental_dependencies(None, upper)
    asyncio.run(service._sync_column_values([("orders", "status")], mode="incremental"))
    if upper is None:
        source.iter_changed_column_value_batches.assert_not_called()
        values.upsert.assert_not_awaited()
        assert state.value_index_cursor_value is None
    else:
        source.iter_changed_column_value_batches.assert_called_once_with(
            "orders", "status", "updated_at", None, upper
        )
        values.upsert.assert_awaited_once()
        assert state.value_index_cursor_value == service._serialize_cursor(upper)


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
    with patch.object(service, "_sync_column_values", new=AsyncMock()) as sync:
        asyncio.run(service.import_incremental_values())
    sync.assert_awaited_once_with([("orders", "status")], mode="incremental")


def test_incremental_script_uses_saved_catalog_without_reading_yaml():
    indexer = MagicMock(import_full=AsyncMock(), import_incremental_values=AsyncMock())

    @asynccontextmanager
    async def services():
        yield indexer

    with (
        patch("scripts.import_metadata.metadata_import_service", services),
        patch("scripts.import_metadata.parse_metadata_yaml") as parse,
    ):
        asyncio.run(import_file(full=False))
    parse.assert_not_called()
    indexer.import_full.assert_not_awaited()
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
def test_semantic_build_embeds_unique_texts_without_database_access(kind, write_fails):
    item = SimpleNamespace(
        name="amount",
        description=" 金额 ",
        alias=["金额", "amount", "总额", " "],
        t_name="orders",
        type="DECIMAL",
        examples=[],
        index_values=False,
        reference_t_name=None,
        reference_c_name=None,
        relevant_columns=[],
    )
    repo = MagicMock()

    async def write(documents):
        if write_fails:
            raise RuntimeError("write failed")

    index = MagicMock(write_documents=AsyncMock(side_effect=write))
    embedding = MagicMock(
        aembed_documents=AsyncMock(return_value=[[1.0], [2.0], [3.0]])
    )
    service = MetaIndexService(repo, MagicMock(), index, index, embedding, MagicMock())

    async def build():
        if kind == "column":
            await service._build_column_indexes([cast(ColumnInfo, item)])
        else:
            await service._build_metric_indexes([cast(MetricInfo, item)])

    if write_fails:
        with pytest.raises(RuntimeError, match="write failed"):
            asyncio.run(build())
    else:
        asyncio.run(build())
    assert repo.mock_calls == []
    embedding.aembed_documents.assert_awaited_once_with(["amount", "总额", "金额"])
    documents = index.write_documents.call_args.args[0]
    payload_keys = (
        (
            "t_name",
            "name",
            "type",
            "examples",
            "description",
            "alias",
            "index_values",
            "reference_t_name",
            "reference_c_name",
        )
        if kind == "column"
        else ("name", "description", "relevant_columns", "alias")
    )
    assert all(
        doc.payload == {key: getattr(item, key) for key in payload_keys}
        for doc in documents
    )
    assert [(doc.text, doc.text_type, doc.embedding) for doc in documents] == [
        ("amount", "name", [1.0]),
        ("总额", "alias", [2.0]),
        ("金额", "description", [3.0]),
    ]


def test_import_normalizes_metadata_before_catalog_and_index_building():
    config = parse_metadata_yaml(VALID_YAML)
    config.tables[0].columns[0].alias = ["z", "a", "z"]
    config.metrics[0].alias = ["b", "a", "b"]
    config.metrics[0].relevant_columns *= 2
    repo, source, index, _ = full_import_dependencies()
    source.get_table_columns_sample_values.return_value = {
        "status": [Decimal("2.5"), Decimal("1.5")]
    }
    asyncio.run(index.import_full(config))
    _, columns, metrics = repo.replace_catalog.call_args.args
    assert columns[0].alias == ["a", "z"]
    assert columns[0].examples == [1.5, 2.5]
    assert metrics[0].alias == ["a", "b"]
    assert metrics[0].relevant_columns == [{"t_name": "orders", "c_name": "status"}]


@pytest.mark.parametrize("failure_stage", ["ensure_index", "refresh"])
def test_index_setup_or_refresh_failure_preserves_watermark(failure_stage):
    service, repo, _, values, column = incremental_dependencies(10, 20)
    getattr(values, failure_stage).side_effect = RuntimeError(failure_stage)
    with pytest.raises(RuntimeError, match=failure_stage):
        asyncio.run(
            service._sync_column_values([("orders", "status")], mode="incremental")
        )
    assert column.value_index_cursor_value == service._serialize_cursor(10)
    repo.update_value_index_cursor.assert_not_awaited()


@pytest.mark.parametrize("cursor_column, upper", [("updated_at", 20), (None, None)])
def test_full_value_scan_commits_watermark_after_refresh(cursor_column, upper):
    service, repo, source, values, column = incremental_dependencies(None, upper)
    repo.get_table_info.return_value.value_index_cursor_column = cursor_column

    async def batches(*args):
        yield ["paid", None]

    source.iter_column_value_batches = MagicMock(side_effect=batches)
    original_update = repo.update_value_index_cursor.side_effect

    async def update(*args):
        values.refresh.assert_awaited_once()
        await original_update(*args)

    repo.update_value_index_cursor.side_effect = update
    asyncio.run(service._sync_column_values([("orders", "status")], mode="full"))
    source.iter_column_value_batches.assert_called_once_with("orders", "status")
    source.iter_changed_column_value_batches.assert_not_called()
    if upper is None:
        repo.update_value_index_cursor.assert_not_awaited()
        assert column.value_index_cursor_value is None
    else:
        assert column.value_index_cursor_value == service._serialize_cursor(upper)


def test_watermark_update_only_changes_target_column():
    session = MagicMock(execute=AsyncMock())
    asyncio.run(
        MetaPGRepo(session).update_value_index_cursor(
            "orders", "status", {"type": "int", "value": 20}
        )
    )
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert str(statement).startswith("UPDATE column_info SET value_index_cursor_value=")
    assert "column_info.t_name =" in str(statement)
    assert "column_info.name =" in str(statement)
    assert statement.params["value_index_cursor_value"] == {"type": "int", "value": 20}
    assert "orders" in statement.params.values()
    assert "status" in statement.params.values()


def test_source_and_index_io_run_outside_postgres_transactions():
    service, repo, source, values, _ = incremental_dependencies(10, 20)
    in_transaction = False
    transactions = []

    @asynccontextmanager
    async def tracked_transaction():
        nonlocal in_transaction
        assert not in_transaction
        in_transaction = True
        transactions.append(True)
        try:
            yield
        finally:
            in_transaction = False

    async def upper_bound(*args):
        assert not in_transaction
        return 20

    async def write(*args):
        assert not in_transaction

    original_update = repo.update_value_index_cursor.side_effect

    async def update(*args):
        assert in_transaction
        values.refresh.assert_awaited_once()
        await original_update(*args)

    repo.session.begin = tracked_transaction
    repo.update_value_index_cursor.side_effect = update
    source.get_value_sync_upper_bound.side_effect = upper_bound
    values.ensure_index.side_effect = write
    values.upsert.side_effect = write
    values.refresh.side_effect = write
    asyncio.run(service._sync_column_values([("orders", "status")], mode="incremental"))
    assert len(transactions) == 2


@pytest.mark.parametrize("kind", ["column", "metric"])
@pytest.mark.parametrize("count", [0, 32, 33])
def test_semantic_indexes_batch_across_resources(kind, count):
    items = [
        SimpleNamespace(
            name=f"field_{i:03d}",
            description="说明",
            alias=["说明", " "],
            t_name="orders",
            type="TEXT",
            examples=[],
            index_values=False,
            reference_t_name=None,
            reference_c_name=None,
            relevant_columns=[],
        )
        for i in range(count)
    ]
    expected_texts = [text for item in items for text in (item.name, "说明")]
    offset = 0

    async def embed(texts):
        nonlocal offset
        vectors = [[float(i)] for i in range(offset, offset + len(texts))]
        offset += len(texts)
        return vectors

    embedding = MagicMock(aembed_documents=AsyncMock(side_effect=embed))
    index = MagicMock(write_documents=AsyncMock())
    service = MetaIndexService(
        MagicMock(), MagicMock(), index, index, embedding, MagicMock()
    )
    if kind == "column":
        asyncio.run(service._build_column_indexes(cast(list[ColumnInfo], items)))
    else:
        asyncio.run(service._build_metric_indexes(cast(list[MetricInfo], items)))

    assert [call.args[0] for call in embedding.aembed_documents.await_args_list] == [
        expected_texts[i : i + 64] for i in range(0, len(expected_texts), 64)
    ]
    index.write_documents.assert_awaited_once()
    documents = index.write_documents.await_args.args[0]
    assert [doc.text for doc in documents] == expected_texts
    assert [doc.embedding for doc in documents] == [
        [float(i)] for i in range(count * 2)
    ]
    assert len({doc.id for doc in documents}) == count * 2
    for item, pair in zip(
        items, [documents[i : i + 2] for i in range(0, len(documents), 2)], strict=True
    ):
        key = (
            column_resource_key(item.t_name, item.name)
            if kind == "column"
            else item.name
        )
        assert all(
            doc.resource_key == key and doc.payload["name"] == item.name for doc in pair
        )
        assert [doc.text_type for doc in pair] == ["name", "description"]


def test_semantic_embedding_failure_does_not_write_incomplete_documents():
    embedding = MagicMock(
        aembed_documents=AsyncMock(
            side_effect=[[[1.0]], RuntimeError("embedding failed")]
        )
    )
    index = MagicMock(write_documents=AsyncMock())
    service = MetaIndexService(
        MagicMock(), MagicMock(), index, index, embedding, MagicMock()
    )
    service._embedding_batch_size = 1
    item = SimpleNamespace(
        name="amount", description="金额", alias=[], relevant_columns=[]
    )
    with pytest.raises(RuntimeError, match="embedding failed"):
        asyncio.run(service._build_metric_indexes([cast(MetricInfo, item)]))
    index.write_documents.assert_not_awaited()
