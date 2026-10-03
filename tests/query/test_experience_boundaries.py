"""经验版本复核和失效在模块拥有的事务中执行。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.identity.contracts import AssetAccessPolicy, AssetIdentity
from app.metadata.contracts import AssetVersions
from app.query import QueryExperienceService
from app.query.experiences.invalidation import QueryExperienceInvalidationService
from app.query.experiences.recall import _SemanticRecall
from app.shared.config.app_config import cfg
from app.shared.contracts.assets import asset_resource_key


def test_recall_reads_metadata_outside_query_transactions_and_excludes_stale_assets():
    active = False
    sessions = 0
    opened_sessions = 0

    @asynccontextmanager
    async def transaction():
        nonlocal active
        assert not active
        active = True
        try:
            yield
        finally:
            active = False

    @asynccontextmanager
    async def session():
        nonlocal sessions, opened_sessions
        assert sessions == 0
        sessions += 1
        opened_sessions += 1
        try:
            yield MagicMock(begin=transaction)
        finally:
            sessions -= 1

    def experience(table, version):
        return SimpleNamespace(
            id=uuid4(),
            status="active",
            revision=2,
            purposes=["统计"],
            sql_template="SELECT 1",
            assets=[
                SimpleNamespace(
                    kind="table",
                    database_name=cfg.doris.database,
                    table_name=table,
                    column_name=None,
                    resource_key=asset_resource_key(
                        cfg.query.data_source, cfg.doris.database, table
                    ),
                    meta_version=version,
                )
            ],
        )

    current, stale, deleted = (
        experience("orders", 3),
        experience("orders", 2),
        experience("deleted", 1),
    )
    items = [current, stale, deleted]
    repo = MagicMock(
        get_many=AsyncMock(return_value=items),
        disable_for_metadata_change=AsyncMock(
            return_value={stale.id: 3, deleted.id: 3}
        ),
    )

    async def versions(tables, columns):
        assert not active
        assert sessions == 0
        assert tables == {"orders", "deleted"}
        assert columns == set()
        return AssetVersions({"orders": 3}, {})

    scheduler = MagicMock()
    scheduler.enqueue.side_effect = lambda *args: (
        pytest.fail("enqueue before commit and session close")
        if active or sessions
        else None
    )
    service = QueryExperienceService(
        MagicMock(session=session),
        MagicMock(asset_versions=AsyncMock(side_effect=versions)),
        MagicMock(),
        MagicMock(),
        config=cfg.query,
        database_name=cfg.doris.database,
        index_scheduler=scheduler,
    )
    service._semantic_recall = AsyncMock(
        return_value=_SemanticRecall("success", {item.id: 1.0 for item in items})
    )
    policy = AssetAccessPolicy(
        7,
        "reader",
        "fingerprint",
        frozenset({AssetIdentity(cfg.query.data_source, cfg.doris.database)}),
    )
    with (
        patch(
            "app.query.experiences.recall.QueryExperiencePGRepo",
            return_value=repo,
        ),
    ):
        result = asyncio.run(service.recall(policy=policy, query="订单", limit=3))
    assert [item.id for item in result.results] == [current.id]
    repo.disable_for_metadata_change.assert_awaited_once_with({stale.id, deleted.id})
    assert scheduler.enqueue.call_count == 2
    assert opened_sessions == 2
    assert sessions == 0


@pytest.mark.parametrize("role,fingerprint", [(None, None), ("reader", None)])
def test_recall_without_query_identity_never_opens_storage_or_searches(
    role, fingerprint
):
    postgres = MagicMock()
    service = QueryExperienceService(
        postgres,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        config=cfg.query,
        database_name=cfg.doris.database,
        index_scheduler=MagicMock(),
    )
    service._semantic_recall = AsyncMock()
    result = asyncio.run(
        service.recall(AssetAccessPolicy(7, role, fingerprint), "订单", 3)
    )
    assert result.status == "success"
    assert result.results == []
    service._semantic_recall.assert_not_awaited()
    postgres.session.assert_not_called()


def test_failed_experience_search_never_opens_query_storage():
    postgres = MagicMock()
    service = QueryExperienceService(
        postgres,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        config=cfg.query,
        database_name=cfg.doris.database,
        index_scheduler=MagicMock(),
    )
    service._semantic_recall = AsyncMock(return_value=_SemanticRecall("failed", {}))
    result = asyncio.run(
        service.recall(AssetAccessPolicy(7, "reader", "fingerprint"), "订单", 3)
    )
    assert result.status == "failed"
    assert result.results == []
    postgres.session.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_invalidation_owns_query_transaction_and_only_schedules_committed_changes(
    fails,
):
    postgres = MagicMock()
    session = MagicMock()
    postgres.session.return_value.__aenter__.return_value = session
    transaction = session.begin.return_value
    item_id = uuid4()
    repo = MagicMock(
        disable_for_changed_resources=AsyncMock(
            side_effect=RuntimeError("disable") if fails else None,
            return_value={item_id: 4},
        )
    )
    scheduler = MagicMock()

    def enqueue(*args):
        transaction.__aexit__.assert_awaited_once()
        postgres.session.return_value.__aexit__.assert_awaited_once()

    scheduler.enqueue.side_effect = enqueue
    service = QueryExperienceInvalidationService(
        postgres, scheduler, data_source="doris", database_name="analytics"
    )
    with patch(
        "app.query.experiences.invalidation.QueryExperiencePGRepo",
        return_value=repo,
    ):
        operation = service.invalidate_assets(
            table_names={"orders"}, column_keys={("orders", "amount")}
        )
        if fails:
            with pytest.raises(RuntimeError, match="disable"):
                asyncio.run(operation)
            scheduler.enqueue.assert_not_called()
        else:
            assert asyncio.run(operation) == [item_id]
            scheduler.enqueue.assert_called_once_with(item_id, 4)
    repo.disable_for_changed_resources.assert_awaited_once_with(
        {
            asset_resource_key("doris", "analytics", "orders"),
            asset_resource_key("doris", "analytics", "orders", "amount"),
        }
    )
