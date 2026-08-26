"""元数据检索索引增量同步服务测试"""

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.metadata.api.meta.schemas import ValueIndexSyncStateResponse
from app.metadata.models.catalog import ColumnInfo, ValueIndexSyncState
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.source_doris import SourceDorisRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.index import MetaIndexService
from app.shared.clients.embedding_client_manager import EmbeddingClient
from tests.identity.test_auth_service import AsyncSessionStub


def build_service(
    meta_repo: MagicMock | None = None,
    *,
    embedding_client: MagicMock | None = None,
    value_repo: MagicMock | None = None,
) -> MetaIndexService:
    """构造元数据检索索引同步服务"""
    meta_repo = meta_repo or MagicMock(spec=MetaPGRepo)
    meta_repo.session = AsyncSessionStub()
    return MetaIndexService(
        meta_repo=cast(MetaPGRepo, meta_repo),
        source_repo=cast(SourceDorisRepo, MagicMock(spec=SourceDorisRepo)),
        column_repo=cast(ColumnESRepo, MagicMock(spec=ColumnESRepo)),
        metric_repo=cast(MetricESRepo, MagicMock(spec=MetricESRepo)),
        embedding_client=cast(
            EmbeddingClient,
            embedding_client or MagicMock(spec=EmbeddingClient),
        ),
        value_repo=cast(
            ValueESRepo,
            value_repo or MagicMock(spec=ValueESRepo),
        ),
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
        description="字段描述",
        examples=[],
        alias=[],
        index_values=index_values,
    )


def build_state(now: datetime) -> ValueIndexSyncState:
    """构造已成功同步的取值索引状态"""
    return ValueIndexSyncState(
        t_name="orders",
        c_name="status",
        cursor_value={"type": "datetime", "value": now.isoformat()},
        status="succeeded",
        active_run_id=None,
        current_generation=uuid4(),
        active_generation=None,
        last_incremental_synced_at=now,
        last_full_synced_at=now,
        last_error=None,
        updated_at=now,
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
        service.sync_column_indexes = AsyncMock(return_value={})

        await service.sync_table_indexes(["orders", "users"])

        meta_repo.list_column_infos_by_table_names.assert_awaited_once_with(
            ["orders", "users"],
            index_values=None,
        )
        service.sync_column_indexes.assert_awaited_once_with(
            [("orders", "id"), ("orders", "amount"), ("users", "id")]
        )

    async def test_sync_table_values_only_selects_enabled_columns(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.list_column_infos_by_table_names = AsyncMock(
            return_value=[build_column("orders", "status", index_values=True)]
        )
        service = build_service(meta_repo)
        service.sync_column_values = AsyncMock(return_value={})

        await service.sync_table_values(["orders"], mode="incremental")

        meta_repo.list_column_infos_by_table_names.assert_awaited_once_with(
            ["orders"],
            index_values=True,
        )
        service.sync_column_values.assert_awaited_once_with(
            [("orders", "status")],
            mode="incremental",
        )

    async def test_semantic_delta_skips_unchanged_documents(self) -> None:
        embedding_client = MagicMock(spec=EmbeddingClient)
        embedding_client.aembed_documents = AsyncMock()
        service = build_service(embedding_client=embedding_client)
        targets = service._target_semantic_documents(
            "column",
            '["orders","amount"]',
            3,
            {"name": "amount", "alias": ["GMV"]},
            "amount",
            "订单金额",
            ["GMV"],
        )

        delta, embedded_count = await service._semantic_delta(targets, targets)

        self.assertEqual(delta.create, [])
        self.assertEqual(delta.update, [])
        self.assertEqual(delta.delete_ids, [])
        self.assertEqual(delta.unchanged_count, 3)
        self.assertEqual(embedded_count, 0)
        embedding_client.aembed_documents.assert_not_awaited()

    async def test_alias_reorder_does_not_generate_delta(self) -> None:
        service = build_service()
        first = service._target_semantic_documents(
            "metric",
            "gmv",
            2,
            {"name": "gmv", "alias": sorted(["销售额", "成交额"])},
            "gmv",
            "商品交易总额",
            ["销售额", "成交额"],
        )
        reordered = service._target_semantic_documents(
            "metric",
            "gmv",
            2,
            {"name": "gmv", "alias": sorted(["成交额", "销售额"])},
            "gmv",
            "商品交易总额",
            ["成交额", "销售额"],
        )

        delta, embedded_count = await service._semantic_delta(reordered, first)

        self.assertEqual(delta.unchanged_count, 4)
        self.assertFalse(delta.create or delta.update or delta.delete_ids)
        self.assertEqual(embedded_count, 0)

    async def test_payload_only_change_reuses_existing_embeddings(self) -> None:
        embedding_client = MagicMock(spec=EmbeddingClient)
        embedding_client.aembed_documents = AsyncMock()
        service = build_service(embedding_client=embedding_client)
        current = service._target_semantic_documents(
            "column",
            '["orders","amount"]',
            1,
            {"type": "INT"},
            "amount",
            "订单金额",
            [],
        )
        targets = service._target_semantic_documents(
            "column",
            '["orders","amount"]',
            2,
            {"type": "DECIMAL"},
            "amount",
            "订单金额",
            [],
        )

        delta, embedded_count = await service._semantic_delta(targets, current)

        self.assertEqual(len(delta.update), 2)
        self.assertTrue(all(item.embedding is None for item in delta.update))
        self.assertEqual(embedded_count, 0)
        embedding_client.aembed_documents.assert_not_awaited()

    async def test_new_alias_only_embeds_new_text(self) -> None:
        embedding_client = MagicMock(spec=EmbeddingClient)
        embedding_client.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
        service = build_service(embedding_client=embedding_client)
        current = service._target_semantic_documents(
            "metric",
            "gmv",
            1,
            {"alias": []},
            "gmv",
            "商品交易总额",
            [],
        )
        targets = service._target_semantic_documents(
            "metric",
            "gmv",
            2,
            {"alias": ["成交额"]},
            "gmv",
            "商品交易总额",
            ["成交额"],
        )

        delta, embedded_count = await service._semantic_delta(targets, current)

        self.assertEqual(len(delta.create), 1)
        self.assertEqual(len(delta.update), 2)
        self.assertEqual(delta.create[0].embedding, [0.1, 0.2])
        self.assertTrue(all(item.embedding is None for item in delta.update))
        self.assertEqual(embedded_count, 1)
        embedding_client.aembed_documents.assert_awaited_once_with(["成交额"])

    async def test_embedding_revision_change_reembeds_existing_text(self) -> None:
        embedding_client = MagicMock(spec=EmbeddingClient)
        embedding_client.aembed_documents = AsyncMock(return_value=[[0.3, 0.4]])
        service = build_service(embedding_client=embedding_client)
        targets = service._target_semantic_documents(
            "metric",
            "gmv",
            1,
            {"name": "gmv"},
            "gmv",
            "",
            [],
        )
        current = [replace(targets[0], embedding_revision="旧模型:v0")]

        delta, embedded_count = await service._semantic_delta(targets, current)

        self.assertEqual(len(delta.update), 1)
        self.assertEqual(delta.update[0].embedding, [0.3, 0.4])
        self.assertEqual(embedded_count, 1)

    async def test_clear_value_index_deletes_documents_and_state(self) -> None:
        meta_repo = MagicMock(spec=MetaPGRepo)
        meta_repo.delete_value_index_state = AsyncMock()
        value_repo = MagicMock(spec=ValueESRepo)
        value_repo.delete_by_column = AsyncMock(return_value=7)
        service = build_service(meta_repo, value_repo=value_repo)
        column = build_column("orders", "status")

        result = await service._clear_value_index(column)

        self.assertEqual(result.mode, "clear")
        self.assertEqual(result.removed_count, 7)
        value_repo.delete_by_column.assert_awaited_once_with("orders", "status")
        meta_repo.delete_value_index_state.assert_awaited_once_with(
            "orders",
            "status",
        )

    def test_value_sync_mode_selects_full_or_incremental(self) -> None:
        now = datetime.now(UTC)
        state = build_state(now)
        cursor_column = "dw_update_time"

        self.assertEqual(
            MetaIndexService._select_value_sync_mode(
                cursor_column,
                state,
                requested_mode="incremental",
            ),
            "incremental",
        )
        self.assertEqual(
            MetaIndexService._select_value_sync_mode(
                cursor_column,
                state,
                requested_mode="full",
            ),
            "full",
        )
        self.assertEqual(
            MetaIndexService._select_value_sync_mode(
                cursor_column,
                None,
                requested_mode="full",
            ),
            "full",
        )
        with self.assertRaisesRegex(RuntimeError, "缺少游标配置"):
            MetaIndexService._select_value_sync_mode(
                None,
                state,
                requested_mode="incremental",
            )
        with self.assertRaisesRegex(RuntimeError, "缺少全量同步状态"):
            MetaIndexService._select_value_sync_mode(
                cursor_column,
                None,
                requested_mode="incremental",
            )

    def test_value_state_returns_latest_success_time(self) -> None:
        now = datetime.now(UTC)
        state = build_state(now)
        state.last_full_synced_at = now + timedelta(minutes=5)

        self.assertEqual(
            state.last_synced_at,
            now + timedelta(minutes=5),
        )
        self.assertEqual(state.last_sync_mode, "full")

        state.last_incremental_synced_at = now + timedelta(minutes=10)

        self.assertEqual(state.last_sync_mode, "incremental")
        response = ValueIndexSyncStateResponse.model_validate(state)
        self.assertEqual(response.last_sync_mode, "incremental")

    def test_datetime_cursor_roundtrip_and_lookback(self) -> None:
        cursor = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
        serialized = MetaIndexService._serialize_cursor(cursor)

        restored = MetaIndexService._deserialize_cursor(serialized)

        self.assertEqual(restored, cursor)
        self.assertEqual(
            MetaIndexService._lookback_lower_bound(restored, 300),
            cursor - timedelta(seconds=300),
        )

    def test_date_cursor_lookback_includes_previous_day(self) -> None:
        cursor = date(2026, 8, 24)

        lower_bound = MetaIndexService._lookback_lower_bound(cursor, 300)

        self.assertEqual(lower_bound, date(2026, 8, 23))


if __name__ == "__main__":
    unittest.main()
