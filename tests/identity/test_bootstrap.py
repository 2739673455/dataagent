"""预定义身份初始化的重试与授权范围。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.identity.repositories.doris_role import DorisRoleRepository
from app.identity.services.credential import DorisCredentialCipher
from scripts.bootstrap_users import bootstrap


def test_doris_preset_grants_only_business_database_select():
    connection = MagicMock(exec_driver_sql=AsyncMock())

    @asynccontextmanager
    async def connect():
        yield connection

    async def run():
        repo = DorisRoleRepository(MagicMock(connection=connect))
        await repo.ensure_role_identity(
            role_name="dataagent_admin",
            query_user="dataagent_admin_query",
            password="fixed_generated_password",
            workload_group="normal",
            database="ecommerce",
        )
        sql = [call.args[0] for call in connection.exec_driver_sql.await_args_list]
        assert (
            "GRANT SELECT_PRIV ON `internal`.`ecommerce`.* TO ROLE `dataagent_admin`"
            in sql
        )
        assert (
            "GRANT USAGE_PRIV ON WORKLOAD GROUP `normal` TO ROLE `dataagent_admin`"
            in sql
        )
        assert "CREATE ROLE IF NOT EXISTS `dataagent_admin`" in sql
        assert any(
            statement.startswith("CREATE USER IF NOT EXISTS") for statement in sql
        )
        assert not any(
            "ADMIN_PRIV" in statement or "*.*.*" in statement for statement in sql
        )
        connection.exec_driver_sql.reset_mock()
        with pytest.raises(ValueError):
            await repo.ensure_role_identity(
                role_name="injected'; DROP ROLE other",
                query_user="query",
                password="password",
                workload_group="normal",
                database="ecommerce",
            )
        connection.exec_driver_sql.assert_not_awaited()

    asyncio.run(run())


def test_retry_preserves_credential_and_publishes_user_after_doris_succeeds():
    from app.shared.config.app_config import cfg

    cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    saved_identity = None
    users_published = []
    events = []

    async def execute(statement):
        nonlocal saved_identity
        values = statement.compile().params
        if statement.table.name == "doris_query_identities":
            if saved_identity is None:
                saved_identity = SimpleNamespace(
                    encrypted_password=values["encrypted_password"]
                )
        else:
            assert events[-1] == "doris-ready"
            users_published.append(values["id"])

    @asynccontextmanager
    async def begin():
        yield
        events.append("commit")

    session = MagicMock(begin=begin, execute=AsyncMock(side_effect=execute))
    session.get = AsyncMock(side_effect=lambda *args: saved_identity)

    @asynccontextmanager
    async def sessions():
        yield session

    postgres = MagicMock(session=sessions, init_tables=AsyncMock(), close=AsyncMock())
    doris = MagicMock(close=AsyncMock())
    credentials = []

    async def provision(**kwargs):
        assert events[-1] == "commit"
        assert saved_identity is not None
        assert cipher.decrypt(saved_identity.encrypted_password) == kwargs["password"]
        credentials.append(kwargs["password"])
        if len(credentials) == 1:
            raise RuntimeError("Doris unavailable")
        events.append("doris-ready")

    async def run():
        with pytest.raises(RuntimeError, match="Doris unavailable"):
            await bootstrap()
        assert users_published == []
        await bootstrap()
        assert credentials[0] == credentials[1]
        assert users_published == [1]
        assert postgres.close.await_count == 2
        assert doris.close.await_count == 2

    with (
        patch(
            "app.shared.clients.postgres_client_manager.PostgresClientManager",
            return_value=postgres,
        ),
        patch(
            "app.shared.clients.doris_client_manager.DorisClientManager",
            return_value=doris,
        ),
        patch.object(
            DorisRoleRepository,
            "ensure_role_identity",
            new=AsyncMock(side_effect=provision),
        ),
    ):
        asyncio.run(run())
