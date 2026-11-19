"""PostgreSQL 实时权限解析、变化版本和管理写入边界。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.identity import errors
from app.identity.contracts import AssetIdentity
from app.identity.models.business_pg import (
    BusinessPgAuthorizationSnapshot,
    BusinessPgQueryIdentity,
    BusinessPgSelectGrant,
)
from app.identity.repositories.business_pg_role import validate_policy_predicate
from app.identity.roles.authorization import AuthorizationService
from app.identity.roles.permissions import BusinessPgPermissionService


def snapshot(*grants, row_restricted_tables=frozenset()):
    return BusinessPgAuthorizationSnapshot(
        grants=tuple(
            BusinessPgSelectGrant("reader", "business_pg", "ecommerce", table, column)
            for table, column in grants
        ),
        fingerprint=str((grants, row_restricted_tables)),
        row_restricted_tables=row_restricted_tables,
    )


def test_column_table_and_rls_flags_preserve_scope():
    policy = AuthorizationService.policy_from_snapshot(
        7,
        BusinessPgQueryIdentity(role_name="reader", authorization_fingerprint="a" * 64),
        snapshot(
            ("orders", None),
            ("payments", "amount"),
            row_restricted_tables=frozenset({"orders"}),
        ),
    )
    assert policy.allows(AssetIdentity("business_pg", "ecommerce", "orders", "any"))
    assert policy.is_visible(AssetIdentity("business_pg", "ecommerce", "payments"))
    assert not policy.allows(AssetIdentity("business_pg", "ecommerce", "payments"))
    assert not policy.allows(
        AssetIdentity("business_pg", "ecommerce", "payments", "secret")
    )
    assert policy.row_restricted_tables == frozenset({"orders"})


def test_policy_predicate_accepts_only_row_expressions():
    validate = validate_policy_predicate
    assert validate("region = 'east' AND id >= 10") == "region = 'east' AND id >= 10"
    with pytest.raises(errors.InvalidBusinessPgPermissionError):
        validate("SELECT true")
    with pytest.raises(errors.InvalidBusinessPgPermissionError):
        validate("some_function()")


def setup_services():
    identity = BusinessPgQueryIdentity(
        role_name="reader", query_user="query_reader", authorization_fingerprint=None
    )

    @asynccontextmanager
    async def transaction():
        yield

    repo = MagicMock(
        session=MagicMock(begin=transaction),
        lock_query_identity=AsyncMock(return_value=identity),
        lock_security_mutation=AsyncMock(),
        flush=AsyncMock(),
    )
    business_pg = MagicMock(
        read_authorization=AsyncMock(return_value=snapshot()),
        grant_select=AsyncMock(),
        revoke_select=AsyncMock(),
        list_table_columns=AsyncMock(return_value=("amount",)),
    )
    auth = AuthorizationService(
        repo, business_pg, data_source="business_pg", database="ecommerce"
    )
    permissions = BusinessPgPermissionService(
        repo,
        business_pg,
        data_source="business_pg",
        catalog="public",
        database="ecommerce",
    )
    return identity, repo, business_pg, auth, permissions


def test_restored_permissions_restore_fingerprint_and_failures_never_use_old_grants():
    async def run():
        identity, repo, business_pg, auth, _ = setup_services()
        initial = identity.authorization_fingerprint
        first = await auth.get_role_asset_policy(7, "reader")
        assert first.authorization_fingerprint != initial
        again = await auth.get_role_asset_policy(7, "reader")
        assert first == again
        business_pg.read_authorization.return_value = snapshot(("orders", "amount"))
        changed = await auth.get_role_asset_policy(7, "reader")
        assert changed.authorization_fingerprint != first.authorization_fingerprint
        assert changed.allows(
            AssetIdentity("business_pg", "ecommerce", "orders", "amount")
        )
        business_pg.read_authorization.return_value = snapshot()
        revoked = await auth.get_role_asset_policy(7, "reader")
        assert not revoked.grants
        assert revoked.authorization_fingerprint == first.authorization_fingerprint
        assert revoked.authorization_fingerprint != changed.authorization_fingerprint
        fingerprint = identity.authorization_fingerprint
        business_pg.read_authorization.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await auth.get_role_asset_policy(7, "reader")
        assert identity.authorization_fingerprint == fingerprint
        assert repo.flush.await_count == 3

    asyncio.run(run())


def test_read_occurs_after_identity_lock_and_unknown_identity_never_reads_business_pg():
    async def run():
        _, repo, business_pg, auth, _ = setup_services()
        events = []
        identity = repo.lock_query_identity.return_value

        async def lock(role):
            events.append("lock")
            return identity

        async def read(**kwargs):
            events.append("read")
            return snapshot()

        repo.lock_query_identity.side_effect = lock
        business_pg.read_authorization.side_effect = read
        await auth.observe_role("reader")
        assert events == ["lock", "read"]
        repo.lock_query_identity.side_effect = None
        repo.lock_query_identity.return_value = None
        with pytest.raises(errors.RoleNotFoundError):
            await auth.observe_role("reader")
        assert events == ["lock", "read"]

    asyncio.run(run())


def test_revoke_does_not_regrant_after_observation_failure():
    async def run():
        _, _, business_pg, _, service = setup_services()
        business_pg.read_authorization.side_effect = [
            snapshot(),
            RuntimeError("PostgreSQL read failed"),
        ]
        with pytest.raises(RuntimeError):
            await service.revoke_select(
                "reader", table_name="orders", columns=["amount"]
            )
        business_pg.revoke_select.assert_awaited_once()
        business_pg.grant_select.assert_not_awaited()

    asyncio.run(run())


def test_revoke_reports_remaining_effective_grants_at_any_target_depth():
    async def run():
        _, _, business_pg, _, service = setup_services()
        business_pg.read_authorization.return_value = snapshot(("orders", "amount"))
        for table, columns in [(None, []), ("orders", []), ("orders", ["amount"])]:
            with pytest.raises(errors.InvalidBusinessPgPermissionError):
                await service.revoke_select("reader", table_name=table, columns=columns)
        business_pg.revoke_select.reset_mock()
        with pytest.raises(errors.InvalidBusinessPgPermissionError):
            await service.revoke_all_select("reader")
        business_pg.revoke_select.assert_awaited_once_with(
            role_name="reader",
            catalog="public",
            database="ecommerce",
            table=None,
            columns=(),
        )

    asyncio.run(run())
