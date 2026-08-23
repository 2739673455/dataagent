"""元数据检索索引同步服务测试"""

import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.metadata.models import ColumnInfo
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.index import MetaIndexService
from app.shared.clients.embedding_client_manager import EmbeddingClient
from tests.identity.test_auth_service import AsyncSessionStub


def build_service(
    meta_repo: MagicMock,
    value_repo: MagicMock | None = None,
) -> MetaIndexService:
    """构造元数据检索索引同步服务"""
    meta_repo.session = AsyncSessionStub()
    return MetaIndexService(
        meta_repo=cast(MetaPGRepo, meta_repo),
        source_repo=cast(SourceDorisRepo, MagicMock()),
        column_repo=cast(ColumnESRepo, MagicMock()),
        metric_repo=cast(MetricESRepo, MagicMock()),
        embedding_client=cast(EmbeddingClient, MagicMock()),
        value_repo=cast(ValueESRepo, value_repo or MagicMock()),
    )


def build_column(
    t_name: str,
    c_name: str,
    *,
    index_values: bool = False,
) -> ColumnInfo:
    """构造字段信息"""
    return ColumnInfo(
        t_name=t_name,
        name=c_name,
        type="BIGINT",
        description="",
        examples=[],
        alias=[],
        index_values=index_values,
    )


class MetaIndexServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_table_indexes_gets_columns_by_table_names(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.list_column_infos_by_table_names = AsyncMock(
            return_value=[
                build_column("orders", "id"),
                build_column("orders", "amount"),
                build_column("users", "id"),
            ]
        )
        service = build_service(meta_repo)
        sync_column_indexes = AsyncMock(return_value={})

        with patch.object(service, "sync_column_indexes", sync_column_indexes):
            await service.sync_table_indexes(["orders", "users"])

        meta_repo.list_column_infos_by_table_names.assert_awaited_once_with(
            ["orders", "users"],
            index_values=None,
        )
        sync_column_indexes.assert_awaited_once_with(
            [("orders", "id"), ("orders", "amount"), ("users", "id")]
        )

    async def test_sync_table_values_gets_columns_by_table_names(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.list_column_infos_by_table_names = AsyncMock(
            return_value=[build_column("orders", "status", index_values=True)]
        )
        service = build_service(meta_repo)
        sync_column_values = AsyncMock(return_value={})

        with patch.object(service, "sync_column_values", sync_column_values):
            await service.sync_table_values(["orders"])

        meta_repo.list_column_infos_by_table_names.assert_awaited_once_with(
            ["orders"],
            index_values=True,
        )
        sync_column_values.assert_awaited_once_with([("orders", "status")])

    async def test_sync_column_values_skips_disabled_columns(self) -> None:
        disabled_column = build_column("orders", "id")
        enabled_column = build_column("orders", "status", index_values=True)
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.get_column_info = AsyncMock(
            side_effect=[disabled_column, enabled_column]
        )
        value_repo = MagicMock(spec=ValueESRepo)
        value_repo.ensure_index = AsyncMock()
        value_repo.delete_by_column = AsyncMock()
        service = build_service(meta_repo, value_repo)
        sync_column_values = AsyncMock(return_value=2)

        with patch.object(service, "_sync_column_values", sync_column_values):
            results = await service.sync_column_values(
                [("orders", "id"), ("orders", "status")]
            )

        self.assertEqual(results, {("orders", "status"): 2})
        sync_column_values.assert_awaited_once_with(enabled_column)
        value_repo.delete_by_column.assert_awaited_once_with("orders", "id")
        meta_repo.mark_column_values_syncing.assert_called_once_with(enabled_column)
        meta_repo.mark_column_values_succeeded.assert_called_once_with(enabled_column)


if __name__ == "__main__":
    unittest.main()
