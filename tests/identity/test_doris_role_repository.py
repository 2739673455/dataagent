"""用实际 asyncmy 参数展开检查管理 SQL；不冒充 Doris 集成测试。"""

import asyncio
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from asyncmy.connection import Connection
from asyncmy.cursors import Cursor
from sqlalchemy.dialects.mysql.asyncmy import MySQLDialect_asyncmy

from app.identity.repositories.doris_role import DorisRoleRepository
from app.shared.contracts.doris import validate_doris_identifier


class DorisRoleSQLTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        driver = Connection()
        cast(Any, driver).server_status = 0
        self.cursor = Cursor(driver)
        self.statements: list[str] = []

        async def execute(sql: str, parameters: tuple[object, ...] = ()) -> MagicMock:
            self.statements.append(self.cursor.mogrify(sql, parameters))
            result = MagicMock()
            result.mappings.return_value.all.return_value = (
                [{"Grants": "test"}] if sql.startswith("SHOW GRANTS") else []
            )
            return result

        self.connection = MagicMock()
        self.connection.exec_driver_sql = AsyncMock(side_effect=execute)
        provider = MagicMock()
        provider.engine.dialect = MySQLDialect_asyncmy()
        provider.engine.connect.return_value.__aenter__.return_value = self.connection
        self.repository = DorisRoleRepository(provider)

    async def test_identity_values_use_driver_parameters(self) -> None:
        await self.repository.create_role_identity(
            role_name="role`%",
            query_user="user'\\%",
            password="password'\\%",
            workload_group="group`%",
        )
        self.assertEqual(
            self.statements[:2],
            [
                "CREATE ROLE `role``%`",
                "GRANT USAGE_PRIV ON WORKLOAD GROUP `group``%` TO ROLE `role``%`",
            ],
        )
        self.assertEqual(
            self.statements[2],
            "CREATE USER 'user\\'\\\\%'@'%' IDENTIFIED BY 'password\\'\\\\%' DEFAULT ROLE 'role`%'",
        )
        self.connection.exec_driver_sql.assert_awaited_with(
            "CREATE USER %s@%s IDENTIFIED BY %s DEFAULT ROLE %s",
            ("user'\\%", "%", "password'\\%", "role`%"),
        )
        await self.repository.drop_role_identity(
            role_name="role`%", query_user="user'\\%"
        )
        self.assertEqual(self.statements[-2], "DROP USER IF EXISTS 'user\\'\\\\%'@'%'")
        self.assertEqual(self.statements[-1], "DROP ROLE IF EXISTS `role``%`")

    async def test_identifiers_and_predicate_keep_literal_percent(self) -> None:
        args = {
            "role_name": "role%",
            "catalog": "internal",
            "database": "db`%",
            "table": "table%",
        }
        await self.repository.grant_select(**args, columns=["col`%"])
        await self.repository.revoke_select(**args, columns=["col`%"])
        self.assertEqual(
            self.statements[0],
            "GRANT SELECT_PRIV(`col``%`) ON `internal`.`db``%`.`table%` TO ROLE `role%`",
        )
        self.assertEqual(
            self.statements[1],
            "REVOKE SELECT_PRIV(`col``%`) ON `internal`.`db``%`.`table%` FROM ROLE `role%`",
        )
        await self.repository.create_row_policy(
            **args,
            policy_name="policy%",
            policy_type="RESTRICTIVE",
            predicate_sql="name LIKE 'x%s%' AND amount % 2 = 0",
        )
        self.assertIn(
            "USING (name LIKE 'x%s%' AND amount % 2 = 0)", self.statements[-1]
        )
        await self.repository.drop_row_policy(**args, policy_name="policy%")
        self.assertIn("DROP ROW POLICY `policy%`", self.statements[-1])
        await self.repository.list_role_row_policies("role%")
        self.assertEqual(self.statements[-1], "SHOW ROW POLICY FOR ROLE `role%`")

    async def test_authorization_uses_parameterized_user_and_host(self) -> None:
        with patch("app.identity.repositories.doris_role.parse_authorization") as parse:
            await self.repository.read_authorization(
                role_name="role%",
                query_user="user'\\%",
                data_source="doris",
                catalog="internal",
                database="db",
            )
        self.assertEqual(
            self.statements,
            [
                "SET show_user_default_role = false",
                "SHOW GRANTS FOR 'user\\'\\\\%'@'%'",
                "SHOW ROW POLICY FOR ROLE `role%`",
                "SHOW ROW POLICY FOR 'user\\'\\\\%'@'%'",
            ],
        )
        parse.assert_called_once_with(
            {"Grants": "test"},
            [],
            role_name="role%",
            query_user="user'\\%",
            data_source="doris",
            catalog="internal",
            database="db",
        )

    async def test_cancellation_compensates_created_role(self) -> None:
        self.connection.exec_driver_sql.side_effect = [
            None,
            asyncio.CancelledError(),
            None,
        ]
        with self.assertRaises(asyncio.CancelledError):
            await self.repository.create_role_identity(
                role_name="role",
                query_user="user",
                password="password",
                workload_group="group",
            )
        self.connection.exec_driver_sql.assert_awaited_with(
            "DROP ROLE IF EXISTS `role`", ()
        )

    def test_management_name_validation_still_rejects_sql_characters(self) -> None:
        validate_doris_identifier("valid_name-1")
        for name in ("", "bad`name", "bad'name", "bad\\name", "bad%name"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_doris_identifier(name)
