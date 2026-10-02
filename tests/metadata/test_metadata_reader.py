"""目录读取必须返回独立数据，资产版本查询由元数据模块管理会话。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.metadata.application import MetadataReader
from app.metadata.contracts import AssetVersions, QueryCatalogColumn
from app.metadata.models.catalog import ColumnInfo, TableInfo


def test_query_catalog_is_independent_of_orm_objects_after_session_closes():
    events = []

    @asynccontextmanager
    async def session():
        events.append("open")
        yield MagicMock()
        events.append("close")

    table = TableInfo(name="orders")
    column = ColumnInfo(t_name="orders", name="amount", type="DECIMAL")
    repo = MagicMock(
        list_table_infos=AsyncMock(return_value=[table]),
        list_column_infos=AsyncMock(return_value=[column]),
    )
    with patch("app.metadata.application.resources.MetaPGRepo", return_value=repo):
        snapshot = asyncio.run(
            MetadataReader(MagicMock(session=session)).query_catalog()
        )
    assert events == ["open", "close"]
    table.name = "renamed"
    column.type = "VARCHAR"
    assert snapshot.table_names == ("orders",)
    assert snapshot.columns == (QueryCatalogColumn("orders", "amount", "DECIMAL"),)


def test_asset_versions_close_the_metadata_session_and_preserve_missing_assets():
    postgres = MagicMock()
    session = postgres.session.return_value.__aenter__.return_value
    tables = MagicMock(tuples=lambda: [("orders", 3)])
    columns = MagicMock(tuples=lambda: [("orders", "amount", 5)])
    session.execute = AsyncMock(side_effect=[tables, columns])
    reader = MetadataReader(postgres)
    actual = asyncio.run(
        reader.asset_versions(
            {"orders", "removed"}, {("orders", "amount"), ("orders", "removed")}
        )
    )
    assert actual == AssetVersions({"orders": 3}, {("orders", "amount"): 5})
    postgres.session.return_value.__aexit__.assert_awaited_once()
    statements = [call.args[0] for call in session.execute.await_args_list]
    assert [statement.get_final_froms()[0].name for statement in statements] == [
        "table_info",
        "column_info",
    ]
    assert asyncio.run(reader.asset_versions(set(), set())) == AssetVersions({}, {})
    assert postgres.session.call_count == 1
