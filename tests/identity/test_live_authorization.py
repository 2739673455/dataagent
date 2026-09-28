"""Doris 实时权限解析和读取边界。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.identity import errors
from app.identity.models.authorization import AssetIdentity
from app.identity.models.doris import (
    DorisQueryIdentity,
    DorisSelectGrant,
)
from app.identity.repositories.doris_authorization import parse_authorization
from app.identity.services.identity import IdentityService


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


def parse(row=None):
    return parse_authorization(
        raw() if row is None else row,
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
    _, _, doris, auth = setup_services()
    doris.read_authorization.return_value = snapshot
    policy = asyncio.run(
        auth.get_asset_policy(7, doris, data_source="doris", database="ecommerce")
    )
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


@pytest.mark.parametrize(
    "updates",
    [{"GlobalPrivs": "Select_priv"}, {"CatalogPrivs": "internal: Select_priv"}],
)
def test_broad_select_is_limited_to_configured_database(updates):
    snapshot = parse(raw(**updates))
    assert snapshot.grants == (DorisSelectGrant("reader", "doris", "ecommerce"),)


def setup_services():
    identity = DorisQueryIdentity(role_name="reader", query_user="query_reader")

    repo = MagicMock(
        get_user_by_id=AsyncMock(
            return_value=SimpleNamespace(id=7, doris_role_name="reader")
        ),
        get_query_identity=AsyncMock(return_value=identity),
    )
    doris = MagicMock(
        read_authorization=AsyncMock(return_value=parse()),
    )
    auth = IdentityService(repo)
    return identity, repo, doris, auth


def test_permissions_are_read_each_time_and_failures_propagate():
    async def run():
        _, _, doris, auth = setup_services()
        assert not (
            await auth.get_asset_policy(
                7, doris, data_source="doris", database="ecommerce"
            )
        ).grants
        doris.read_authorization.return_value = parse(
            raw(ColPrivs="internal.ecommerce.orders: Select_priv[amount]")
        )
        changed = await auth.get_asset_policy(
            7, doris, data_source="doris", database="ecommerce"
        )
        assert changed.allows(AssetIdentity("doris", "ecommerce", "orders", "amount"))
        doris.read_authorization.return_value = parse()
        assert not (
            await auth.get_asset_policy(
                7, doris, data_source="doris", database="ecommerce"
            )
        ).grants
        doris.read_authorization.side_effect = RuntimeError("unavailable")
        with pytest.raises(RuntimeError):
            await auth.get_asset_policy(
                7, doris, data_source="doris", database="ecommerce"
            )
        assert doris.read_authorization.await_count == 4

    asyncio.run(run())


def test_missing_user_or_identity_stops_before_reading_doris():
    async def run():
        _, repo, doris, auth = setup_services()
        repo.get_query_identity.return_value = None
        with pytest.raises(errors.QueryPrincipalNotConfiguredError):
            await auth.get_asset_policy(
                7, doris, data_source="doris", database="ecommerce"
            )
        repo.get_query_identity.assert_awaited_once_with("reader")
        doris.read_authorization.assert_not_awaited()
        repo.get_user_by_id.return_value = None
        repo.get_query_identity.reset_mock()
        with pytest.raises(errors.UserNotFoundError):
            await auth.get_asset_policy(
                7, doris, data_source="doris", database="ecommerce"
            )
        repo.get_query_identity.assert_not_awaited()
        doris.read_authorization.assert_not_awaited()

    asyncio.run(run())
