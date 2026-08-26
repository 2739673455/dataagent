"""取值索引周期调度筛选测试"""

import unittest
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.metadata.models.catalog import ColumnInfo, TableInfo, ValueIndexSyncState
from app.metadata.repositories.postgres import MetaPGRepo


def build_table(name: str, *, incremental: bool) -> TableInfo:
    """构造表级取值同步配置"""
    return TableInfo(
        name=name,
        role="fact",
        primary_key_columns=[],
        description=name,
        value_index_cursor_column="dw_update_time" if incremental else None,
    )


def build_column(table_name: str, *, enabled: bool = True) -> ColumnInfo:
    """构造字段元数据"""
    return ColumnInfo(
        t_name=table_name,
        name="status",
        type="VARCHAR",
        description="状态",
        examples=[],
        alias=[],
        index_values=enabled,
    )


def build_state(
    table_name: str,
    now: datetime,
    *,
    status: str = "succeeded",
    full_synced_at: datetime | None = None,
) -> ValueIndexSyncState:
    """构造字段取值同步状态"""
    return ValueIndexSyncState(
        t_name=table_name,
        c_name="status",
        cursor_value={"type": "datetime", "value": now.isoformat()},
        status=status,
        active_run_id=None,
        current_generation=uuid4(),
        active_generation=None,
        last_incremental_synced_at=now,
        last_full_synced_at=full_synced_at,
        last_error=None,
        updated_at=now,
    )


class ValueIndexSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_selects_initialized_incremental_and_clear_work(self) -> None:
        now = datetime.now(UTC)
        previous_sync = now - timedelta(days=1)
        rows = [
            (
                build_column("incremental"),
                build_table("incremental", incremental=True),
                build_state("incremental", previous_sync, full_synced_at=now),
            ),
            (
                build_column("manual-only"),
                build_table("manual-only", incremental=False),
                build_state("manual-only", previous_sync, full_synced_at=now),
            ),
            (
                build_column("uninitialized"),
                build_table("uninitialized", incremental=True),
                None,
            ),
            (
                build_column("failed"),
                build_table("failed", incremental=True),
                build_state(
                    "failed",
                    previous_sync,
                    status="failed",
                    full_synced_at=now,
                ),
            ),
            (
                build_column("active"),
                build_table("active", incremental=True),
                build_state("active", now, status="syncing", full_synced_at=now),
            ),
            (
                build_column("stale"),
                build_table("stale", incremental=True),
                build_state(
                    "stale",
                    now - timedelta(hours=2),
                    status="syncing",
                    full_synced_at=now,
                ),
            ),
            (
                build_column("disabled", enabled=False),
                build_table("disabled", incremental=False),
                build_state("disabled", previous_sync, full_synced_at=now),
            ),
        ]
        result = MagicMock()
        result.tuples.return_value = rows
        session = MagicMock(spec=AsyncSession)
        session.execute = AsyncMock(return_value=result)
        repo = MetaPGRepo(cast(AsyncSession, session))

        pending = await repo.claim_pending_value_index_keys(
            now=now,
            stale_before=now - timedelta(hours=2),
            limit=100,
        )

        self.assertEqual(
            pending,
            [
                ("incremental", "status"),
                ("failed", "status"),
                ("stale", "status"),
                ("disabled", "status"),
            ],
        )
        self.assertTrue(
            all(
                state.status == "syncing"
                for _, _, state in rows
                if state is not None and state.t_name in {key[0] for key in pending}
            )
        )

    async def test_table_sync_config_change_invalidates_existing_watermarks(
        self,
    ) -> None:
        existing = build_table("orders", incremental=False)
        existing.meta_version = 3
        updated = build_table("orders", incremental=True)
        session = MagicMock(spec=AsyncSession)
        session.get = AsyncMock(return_value=existing)
        session.merge = AsyncMock()
        session.execute = AsyncMock()
        repo = MetaPGRepo(cast(AsyncSession, session))

        changed = await repo.upsert_table_info(updated)

        self.assertTrue(changed)
        self.assertEqual(updated.meta_version, 4)
        statement = session.execute.await_args.args[0]
        self.assertIn("DELETE FROM value_index_sync_state", str(statement))


if __name__ == "__main__":
    unittest.main()
