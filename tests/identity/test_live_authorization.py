"""Doris 实时权限解析、变化版本和管理写入边界。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.identity import errors
from app.identity.models.doris import (
    DorisQueryIdentity,
    DorisSelectGrant,
)
from app.identity.repositories.doris_authorization import parse_authorization
from app.identity.services.authorization import AssetIdentity, AuthorizationService
from app.identity.services.doris_permission import DorisPermissionService


def raw(**updates):
    return {
        "UserIdentity": "'query_reader'@'%'",
        "Roles": "reader",
        "GlobalPrivs": None,
        "CatalogPrivs": None,
        "DatabasePrivs": "internal.information_schema: Select_priv; internal.mysql: Select_priv",
        "TablePrivs": None,
        "ColPrivs": None,
        **updates,
    }


def parse(row=None, policies=()):
    return parse_authorization(
        raw() if row is None else row,
        policies,
        role_name="reader",
        query_user="query_reader",
        data_source="doris",
        catalog="internal",
        database="ecommerce",
    )


def test_column_table_and_database_grants_preserve_scope():
    snapshot = parse(
        raw(
            TablePrivs="internal.ecommerce.orders: Select_priv, Show_view_priv; external.other.secret: Select_priv",
            ColPrivs="internal.ecommerce.payments: Select_priv[amount, id]",
        )
    )
    assert {(g.table_name, g.column_name) for g in snapshot.grants} == {
        ("orders", None),
        ("payments", "amount"),
        ("payments", "id"),
    }
    assert not snapshot.has_broad_select
    identity = DorisQueryIdentity(
        role_name="reader", authorization_fingerprint="a" * 64
    )
    policy = AuthorizationService.policy_from_snapshot(7, identity, snapshot)
    assert policy.allows(AssetIdentity("doris", "ecommerce", "orders", "any"))
    assert policy.is_visible(AssetIdentity("doris", "ecommerce", "payments"))
    assert not policy.allows(AssetIdentity("doris", "ecommerce", "payments"))
    assert not policy.allows(AssetIdentity("doris", "ecommerce", "payments", "secret"))
    db = parse(raw(DatabasePrivs="internal.ecommerce: Select_priv"))
    assert db.grants == (DorisSelectGrant("reader", "doris", "ecommerce"),)


@pytest.mark.parametrize(
    "updates",
    [
        {"Roles": "reader,extra"},
        {"Roles": ""},
        {"Roles": "wrong"},
        {"UserIdentity": "'query_reader'@'localhost'"},
        {"GlobalPrivs": "Admin_priv"},
        {"ColPrivs": "internal.ecommerce.orders: Select_priv[]"},
        {"ColPrivs": "internal.ecommerce.orders: Unknown_priv[id]"},
        {"TablePrivs": "internal.ecommerce.a.b: Select_priv"},
        {"TablePrivs": "internal.ecommerce.orders: select"},
    ],
)
def test_ambiguous_or_unsafe_authorization_is_rejected(updates):
    with pytest.raises(errors.InvalidDorisPermissionError):
        parse(raw(**updates))


def test_missing_field_is_not_treated_as_empty_grants():
    row = raw()
    del row["ColPrivs"]
    with pytest.raises(errors.InvalidDorisPermissionError):
        parse(row)


def test_fingerprint_ignores_order_but_detects_column_and_row_policy_changes():
    a = raw(
        TablePrivs="internal.ecommerce.b: Select_priv, Show_view_priv; internal.ecommerce.a: Select_priv",
        ColPrivs="internal.ecommerce.c: Select_priv[y, x]",
    )
    b = raw(
        TablePrivs="internal.ecommerce.a: Select_priv; internal.ecommerce.b: Show_view_priv, Select_priv",
        ColPrivs="internal.ecommerce.c: Select_priv[x, y]",
    )
    policies = [
        {
            "PolicyName": "p",
            "CatalogName": "internal",
            "DbName": "ecommerce",
            "TableName": "orders",
            "WherePredicate": "region = 1",
            "FilterType": "RESTRICTIVE",
        }
    ]
    assert (
        parse(a, policies).fingerprint == parse(b, list(reversed(policies))).fingerprint
    )
    assert (
        parse(a, policies).fingerprint
        != parse(a, [{**policies[0], "WherePredicate": "region = 2"}]).fingerprint
    )
    assert (
        parse(a, policies).fingerprint
        == parse(a, [{**policies[0], "Id": 99, "PolicyName": "recreated"}]).fingerprint
    )
    assert parse(a).fingerprint != parse(raw()).fingerprint


@pytest.mark.parametrize(
    "updates",
    [{"GlobalPrivs": "Select_priv"}, {"CatalogPrivs": "internal: Select_priv"}],
)
def test_broad_select_is_explicit_and_limited_to_configured_database(updates):
    snapshot = parse(raw(**updates))
    assert snapshot.has_broad_select
    assert snapshot.grants == (DorisSelectGrant("reader", "doris", "ecommerce"),)


def setup_services():
    identity = DorisQueryIdentity(
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
    doris = MagicMock(
        read_authorization=AsyncMock(return_value=parse()),
        grant_select=AsyncMock(),
        revoke_select=AsyncMock(),
        list_table_columns=AsyncMock(return_value=("amount",)),
    )
    auth = AuthorizationService(repo, doris, data_source="doris", database="ecommerce")
    permissions = DorisPermissionService(
        repo, doris, data_source="doris", catalog="internal", database="ecommerce"
    )
    return identity, repo, doris, auth, permissions


def test_restored_permissions_restore_fingerprint_and_failures_never_use_old_grants():
    async def run():
        identity, repo, doris, auth, _ = setup_services()
        initial = identity.authorization_fingerprint
        first = await auth.get_role_asset_policy(7, "reader")
        assert first.authorization_fingerprint != initial
        again = await auth.get_role_asset_policy(7, "reader")
        assert first == again
        doris.read_authorization.return_value = parse(
            raw(ColPrivs="internal.ecommerce.orders: Select_priv[amount]")
        )
        changed = await auth.get_role_asset_policy(7, "reader")
        assert changed.authorization_fingerprint != first.authorization_fingerprint
        assert changed.allows(AssetIdentity("doris", "ecommerce", "orders", "amount"))
        doris.read_authorization.return_value = parse()
        revoked = await auth.get_role_asset_policy(7, "reader")
        assert not revoked.grants
        assert revoked.authorization_fingerprint == first.authorization_fingerprint
        assert revoked.authorization_fingerprint != changed.authorization_fingerprint
        fingerprint = identity.authorization_fingerprint
        doris.read_authorization.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await auth.get_role_asset_policy(7, "reader")
        assert identity.authorization_fingerprint == fingerprint
        assert repo.flush.await_count == 3

    asyncio.run(run())


def test_read_occurs_after_identity_lock_and_unknown_identity_never_reads_doris():
    async def run():
        _, repo, doris, auth, _ = setup_services()
        events = []
        identity = repo.lock_query_identity.return_value

        async def lock(role):
            events.append("lock")
            return identity

        async def read(**kwargs):
            events.append("read")
            return parse()

        repo.lock_query_identity.side_effect = lock
        doris.read_authorization.side_effect = read
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
        _, _, doris, _, service = setup_services()
        doris.read_authorization.side_effect = [
            parse(),
            RuntimeError("Doris read failed"),
        ]
        with pytest.raises(RuntimeError):
            await service.revoke_select(
                "reader", table_name="orders", columns=["amount"]
            )
        doris.revoke_select.assert_awaited_once()
        doris.grant_select.assert_not_awaited()

    asyncio.run(run())


def test_revoke_reports_remaining_effective_grants_and_refuses_broad_revoke_all():
    async def run():
        _, _, doris, _, service = setup_services()
        doris.read_authorization.return_value = parse(
            raw(DatabasePrivs="internal.ecommerce: Select_priv")
        )
        with pytest.raises(errors.InvalidDorisPermissionError):
            await service.revoke_select(
                "reader", table_name="orders", columns=["amount"]
            )
        doris.revoke_select.reset_mock()
        doris.read_authorization.return_value = parse(raw(GlobalPrivs="Select_priv"))
        with pytest.raises(errors.InvalidDorisPermissionError):
            await service.revoke_all_select("reader")
        doris.revoke_select.assert_not_awaited()

    asyncio.run(run())
