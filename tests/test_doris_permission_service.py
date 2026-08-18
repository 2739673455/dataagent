"""Doris 角色权限管理服务测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.conf.app_config import DorisRoleConfig
from app.entities.auth import DorisRoleAssetGrant
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_role_repo import DorisRoleRepository
from app.services.doris_permission_service import DorisPermissionService
from tests.test_auth_service import AsyncTransactionStub


def role_config() -> DorisRoleConfig:
    """构造稳定 Doris 角色配置"""
    return DorisRoleConfig(
        description="销售角色",
        is_default=True,
        query_user="sales_query",
        query_password="secret",
        workload_group="sales",
    )


class DorisPermissionServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证 Doris 授权与应用可见性投影同步"""

    def setUp(self) -> None:
        self.auth_repo = MagicMock(spec=AuthPGRepo)
        self.auth_repo.transaction.return_value = AsyncTransactionStub()
        self.auth_repo.find_asset_grant = AsyncMock(return_value=None)
        self.auth_repo.add_asset_grant = AsyncMock(side_effect=lambda grant: grant)
        self.auth_repo.delete_asset_grant = AsyncMock()
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
            self.doris_repo,
            {"sales": role_config()},
            data_source="doris",
            catalog="internal",
            database="ecommerce",
        )

    async def test_column_grant_updates_doris_and_each_column_projection(self) -> None:
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
            with self.subTest(predicate=predicate), self.assertRaises(
                auth_error.InvalidDorisPermissionError
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
            DorisRoleRepository.quote_role("sales' OR '1'='1")
