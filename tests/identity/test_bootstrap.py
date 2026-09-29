"""预定义身份初始化的重试与授权范围。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncmy.connection import Connection
from asyncmy.cursors import Cursor
from sqlalchemy.dialects.mysql.asyncmy import dialect

from app.identity.services.credential import DorisCredentialCipher
from scripts.bootstrap_users import RolePreset, bootstrap


@pytest.mark.parametrize(
    ("role", "database", "password"),
    [
        (
            RolePreset("dataagent_admin", "查询权限", "dataagent_admin_query"),
            "ecommerce",
            "fixed_generated_password",
        ),
        (
            RolePreset("role`%; DROP ROLE other", "查询权限", "user'\\%", "group`%"),
            "db`%",
            "password'\\%",
        ),
    ],
)
def test_bootstrap_grants_escaping_and_retry(role, database, password):
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
    credentials = []

    async def execute_doris(statement, parameters):
        if statement.startswith("CREATE ROLE"):
            assert events[-1] == "commit"
            assert saved_identity is not None
            credentials.append(cipher.decrypt(saved_identity.encrypted_password))
            if len(credentials) == 1:
                raise RuntimeError("Doris unavailable")
        if statement.startswith("GRANT ") and " TO %s@%s" in statement:
            events.append("doris-ready")

    connection = MagicMock(
        dialect=dialect(), exec_driver_sql=AsyncMock(side_effect=execute_doris)
    )

    @asynccontextmanager
    async def connect():
        yield connection

    doris = MagicMock(engine=MagicMock(connect=connect), close=AsyncMock())

    async def run():
        with pytest.raises(RuntimeError, match="Doris unavailable"):
            await bootstrap()
        assert users_published == []
        connection.exec_driver_sql.reset_mock()
        await bootstrap()
        assert credentials[0] == credentials[1]
        assert users_published == [1]
        assert postgres.close.await_count == 2
        assert doris.close.await_count == 2

        driver = Connection()
        driver.server_status = 0
        cursor = Cursor(driver)
        rendered = [
            cursor.mogrify(*call.args)
            for call in connection.exec_driver_sql.await_args_list
        ]
        role_sql = "`" + role.name.replace("`", "``") + "`"
        group_sql = "`" + role.workload_group.replace("`", "``") + "`"
        database_sql = "`" + database.replace("`", "``") + "`"
        user_sql = driver.escape(role.query_user)
        password_sql = driver.escape(password)
        assert rendered == [
            f"CREATE ROLE IF NOT EXISTS {role_sql}",
            f"GRANT SELECT_PRIV ON `internal`.{database_sql}.* TO ROLE {role_sql}",
            f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group_sql} TO ROLE {role_sql}",
            (
                f"CREATE USER IF NOT EXISTS {user_sql}@'%' IDENTIFIED BY {password_sql} "
                f"DEFAULT ROLE {driver.escape(role.name)}"
            ),
            f"SET PASSWORD FOR {user_sql}@'%' = PASSWORD({password_sql})",
            f"GRANT {role_sql} TO {user_sql}@'%'",
        ]

    with (
        patch(
            "app.shared.clients.postgres_client_manager.PostgresClientManager",
            return_value=postgres,
        ),
        patch(
            "app.shared.clients.doris_client_manager.DorisClientManager",
            return_value=doris,
        ),
        patch("scripts.bootstrap_users.ROLES", (role,)),
        patch("scripts.bootstrap_users.secrets.token_urlsafe", return_value=password),
        patch.object(cfg.doris, "database", database),
    ):
        asyncio.run(run())
