"""Doris 角色权限管理服务测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import OperationalError

from app.identity import errors as auth_error
from app.identity.models import DorisQueryIdentity, DorisRoleAssetGrant
from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.doris_role import (
    DorisRoleRepository,
    DorisWorkloadGroupNotFoundError,
)
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.doris_permission import DorisPermissionService
from tests.identity.test_auth_service import AsyncSessionStub


def query_identity() -> DorisQueryIdentity:
    """构造稳定 Doris 查询身份"""
    return DorisQueryIdentity(
        role_name="sales",
        description="销售角色",
        is_default=True,
        is_active=True,
        query_user="sales_query",
        encrypted_password="encrypted",
        workload_group="sales",
    )


class DorisPermissionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Doris 授权与应用可见性投影同步"""

    def setUp(self) -> None:
        self.auth_repo = MagicMock(spec=AuthPGRepo)
        self.session = AsyncSessionStub()
        self.auth_repo.session = self.session
        self.auth_repo.find_asset_grant = AsyncMock(return_value=None)
        self.auth_repo.add_asset_grant = AsyncMock(side_effect=lambda grant: grant)
        self.auth_repo.delete_asset_grant = AsyncMock()
        self.identity_repo = MagicMock(spec=DorisQueryIdentityPGRepo)
        self.identity_repo.session = self.session
        self.identity_repo.get = AsyncMock(return_value=query_identity())
        self.doris_repo = MagicMock(spec=DorisRoleRepository)
        self.doris_repo.list_table_columns = AsyncMock(
            return_value=("id", "region", "amount")
        )
        self.doris_repo.grant_select = AsyncMock()
        self.doris_repo.revoke_select = AsyncMock()
        self.doris_repo.create_row_policy = AsyncMock()
        self.doris_repo.drop_row_policy = AsyncMock()
        self.service = DorisPermissionService(
            self.auth_repo,
            self.identity_repo,
            self.doris_repo,
            data_source="doris",
            catalog="internal",
            database="ecommerce",
        )

    def test_rejects_postgres_repositories_from_different_sessions(self) -> None:
        self.identity_repo.session = AsyncSessionStub()

        with self.assertRaisesRegex(ValueError, "必须共享同一数据库会话"):
            DorisPermissionService(
                self.auth_repo,
                self.identity_repo,
                self.doris_repo,
                data_source="doris",
                catalog="internal",
                database="ecommerce",
            )

    async def test_column_grant_updates_doris_and_each_column_projection(self) -> None:
        self.identity_repo.get = AsyncMock(
            side_effect=self._load_identity_in_transaction
        )
        grants = await self.service.grant_select(
            "sales",
            table_name="orders",
            columns=["region", "amount"],
        )

        self.doris_repo.grant_select.assert_awaited_once_with(
            role_name="sales",
            catalog="internal",
            database="ecommerce",
            table="orders",
            columns=("region", "amount"),
        )
        self.assertEqual([grant.column_name for grant in grants], ["region", "amount"])
        self.assertTrue(all(grant.scope == "column" for grant in grants))

    async def test_database_and_table_grants_have_distinct_scopes(self) -> None:
        database_grant = (
            await self.service.grant_select(
                "sales",
                table_name=None,
                columns=[],
            )
        )[0]
        table_grant = (
            await self.service.grant_select(
                "sales",
                table_name="orders",
                columns=[],
            )
        )[0]

        self.assertEqual(database_grant.scope, "database")
        self.assertEqual(table_grant.scope, "table")

    async def test_unknown_column_is_rejected_before_doris(self) -> None:
        with self.assertRaises(auth_error.InvalidDorisPermissionError):
            await self.service.grant_select(
                "sales",
                table_name="orders",
                columns=["secret"],
            )

        self.doris_repo.grant_select.assert_not_awaited()

    async def test_revoke_removes_exact_projection(self) -> None:
        self.identity_repo.get = AsyncMock(
            side_effect=self._load_identity_in_transaction
        )
        persisted = DorisRoleAssetGrant(
            role_name="sales",
            scope="column",
            data_source="doris",
            database_name="ecommerce",
            table_name="orders",
            column_name="amount",
            resource_key="key",
        )
        self.auth_repo.find_asset_grant.return_value = persisted

        await self.service.revoke_select(
            "sales",
            table_name="orders",
            columns=["amount"],
        )

        self.doris_repo.revoke_select.assert_awaited_once()
        self.auth_repo.delete_asset_grant.assert_awaited_once_with(persisted)

    async def _load_identity_in_transaction(
        self,
        _: str,
    ) -> DorisQueryIdentity:
        self.assertTrue(self.session.active)
        return query_identity()

    async def test_row_policy_accepts_target_columns(self) -> None:
        await self.service.create_row_policy(
            "sales",
            policy_name="sales_region",
            table_name="orders",
            policy_type="RESTRICTIVE",
            predicate="region = 'east' AND amount > 0",
        )

        call = self.doris_repo.create_row_policy.await_args.kwargs
        self.assertEqual(call["role_name"], "sales")
        self.assertIn("region", call["predicate_sql"])

    async def test_row_policy_rejects_subquery_and_unknown_column(self) -> None:
        for predicate in (
            "id IN (SELECT id FROM users)",
            "secret = 1",
        ):
            with (
                self.subTest(predicate=predicate),
                self.assertRaises(auth_error.InvalidDorisPermissionError),
            ):
                await self.service.create_row_policy(
                    "sales",
                    policy_name="invalid_policy",
                    table_name="orders",
                    policy_type="RESTRICTIVE",
                    predicate=predicate,
                )

        self.doris_repo.create_row_policy.assert_not_awaited()


class DorisRoleRepositoryIdentifierTest(unittest.TestCase):
    """验证 Doris 管理 SQL 的标识符边界"""

    def test_identifiers_are_quoted_and_injection_is_rejected(self) -> None:
        self.assertEqual(
            DorisRoleRepository.qualified_table("internal", "sales", "orders"),
            "`internal`.`sales`.`orders`",
        )
        with self.assertRaises(ValueError):
            DorisRoleRepository.quote_identifier("orders`; DROP ROLE admin")
        with self.assertRaises(ValueError):
            DorisRoleRepository.quote_role("sales` OR `1`=`1")
        with self.assertRaises(ValueError):
            DorisRoleRepository.quote_role_literal("sales' OR '1'='1")
        with self.assertRaises(ValueError):
            DorisRoleRepository.quote_user("sales' OR '1'='1")
        self.assertEqual(DorisRoleRepository.quote_role("sales"), "`sales`")
        self.assertEqual(DorisRoleRepository.quote_role_literal("sales"), "'sales'")
        self.assertEqual(DorisRoleRepository.quote_user("sales"), "'sales'")


class DorisRoleRepositoryWorkloadGroupTest(unittest.IsolatedAsyncioTestCase):
    """验证 Doris 工作组查询边界"""

    async def test_lists_and_checks_workload_groups_with_parameterized_sql(
        self,
    ) -> None:
        provider = MagicMock()
        connection = MagicMock()
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = ["batch", "normal"]
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = 1
        connection.execute = AsyncMock(side_effect=[list_result, existing_result])
        provider.connection.return_value.__aenter__ = AsyncMock(return_value=connection)
        provider.connection.return_value.__aexit__ = AsyncMock(return_value=False)
        repo = DorisRoleRepository(provider)

        workload_groups = await repo.list_workload_groups()
        exists = await repo.workload_group_exists("normal")

        self.assertEqual(workload_groups, ("batch", "normal"))
        self.assertTrue(exists)
        existence_call = connection.execute.await_args_list[1]
        self.assertIn(":workload_group", str(existence_call.args[0]))
        self.assertEqual(existence_call.args[1], {"workload_group": "normal"})


class DorisRoleRepositoryIdentityTest(unittest.IsolatedAsyncioTestCase):
    """验证 Doris 查询身份创建 SQL 与补偿边界"""

    async def test_create_role_identity_uses_role_literal_for_default_role(
        self,
    ) -> None:
        repo = DorisRoleRepository(MagicMock())
        repo._execute = AsyncMock()  # pyright: ignore[reportPrivateUsage]

        await repo.create_role_identity(
            role_name="sales",
            query_user="sales_query",
            password="generated-password",
            workload_group="normal",
        )

        statements = [call.args[0] for call in repo._execute.await_args_list]  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(
            statements,
            [
                "CREATE ROLE `sales`",
                "GRANT USAGE_PRIV ON WORKLOAD GROUP `normal` TO ROLE `sales`",
                (
                    "CREATE USER 'sales_query' IDENTIFIED BY 'generated-password' "
                    "DEFAULT ROLE 'sales'"
                ),
            ],
        )

    async def test_existing_role_uses_role_literal_for_default_role(self) -> None:
        repo = DorisRoleRepository(MagicMock())
        repo._execute = AsyncMock()  # pyright: ignore[reportPrivateUsage]

        await repo.create_query_user_for_existing_role(
            role_name="sales",
            query_user="sales_query",
            password="generated-password",
            workload_group="normal",
        )

        statements = [call.args[0] for call in repo._execute.await_args_list]  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(
            statements,
            [
                "GRANT USAGE_PRIV ON WORKLOAD GROUP `normal` TO ROLE `sales`",
                (
                    "CREATE USER 'sales_query' IDENTIFIED BY 'generated-password' "
                    "DEFAULT ROLE 'sales'"
                ),
            ],
        )

    async def test_existing_query_user_is_not_deleted_when_creation_fails(
        self,
    ) -> None:
        repo = DorisRoleRepository(MagicMock())
        repo._execute = AsyncMock(  # pyright: ignore[reportPrivateUsage]
            side_effect=[None, None, RuntimeError("user exists"), None]
        )

        with self.assertRaisesRegex(RuntimeError, "user exists"):
            await repo.create_role_identity(
                role_name="sales",
                query_user="sales_query",
                password="generated-password",
                workload_group="normal",
            )

        statements = [call.args[0] for call in repo._execute.await_args_list]  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(statements[-1], "DROP ROLE IF EXISTS `sales`")
        self.assertFalse(
            any(statement.startswith("DROP USER") for statement in statements)
        )

    async def test_missing_workload_group_is_classified_after_compensation(
        self,
    ) -> None:
        repo = DorisRoleRepository(MagicMock())
        missing_group = OperationalError(
            "GRANT",
            {},
            RuntimeError(
                "errCode = 2, detailMessage = Can not find workload group batch"
            ),
        )
        repo._execute = AsyncMock(  # pyright: ignore[reportPrivateUsage]
            side_effect=[None, missing_group, None]
        )

        with self.assertRaises(DorisWorkloadGroupNotFoundError) as context:
            await repo.create_role_identity(
                role_name="sales",
                query_user="sales_query",
                password="generated-password",
                workload_group="batch",
            )

        self.assertEqual(context.exception.workload_group, "batch")
        statements = [call.args[0] for call in repo._execute.await_args_list]  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(statements[-1], "DROP ROLE IF EXISTS `sales`")
