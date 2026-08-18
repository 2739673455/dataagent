"""角色与数据资产授权策略测试"""

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.entities.auth import PlatformRole, Role, RoleAssetGrant, User
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.services.authorization_service import (
    AssetAccessPolicy,
    AssetIdentity,
    AuthorizationService,
    RoleManagementService,
)
from tests.test_auth_service import AsyncTransactionStub


def build_user(role: PlatformRole, user_id: int = 8) -> User:
    """构造授权测试用户"""
    now = datetime.now(UTC)
    return User(
        id=user_id,
        username="policy-user",
        email="policy@example.com",
        password_hash="hash",
        is_active=True,
        created_at=now,
        updated_at=now,
        roles=[Role(name=role.value, description=role.value)],
    )


class AssetAccessPolicyTest(unittest.TestCase):
    """验证层级授权的访问与目录可见语义"""

    def test_database_grant_allows_descendant_tables_and_columns(self) -> None:
        policy = AssetAccessPolicy(
            user_id=1,
            grants=frozenset({AssetIdentity("doris", "sales")}),
        )

        self.assertTrue(policy.allows(AssetIdentity("doris", "sales", "orders")))
        self.assertTrue(
            policy.allows(AssetIdentity("doris", "sales", "orders", "amount"))
        )
        self.assertFalse(
            policy.allows(AssetIdentity("doris", "finance", "payments"))
        )

    def test_column_grant_makes_table_visible_without_allowing_whole_table(self) -> None:
        table = AssetIdentity("doris", "sales", "orders")
        amount = AssetIdentity("doris", "sales", "orders", "amount")
        secret = AssetIdentity("doris", "sales", "orders", "secret_note")
        policy = AssetAccessPolicy(user_id=1, grants=frozenset({amount}))

        self.assertTrue(policy.is_visible(table))
        self.assertFalse(policy.allows(table))
        self.assertTrue(policy.allows(amount))
        self.assertFalse(policy.allows(secret))
        with self.assertRaises(auth_error.AssetAccessDeniedError):
            policy.require(table)

    def test_resource_key_is_unambiguous_for_names_containing_dots(self) -> None:
        left = AssetIdentity("doris", "sales.eu", "orders")
        right = AssetIdentity("doris", "sales", "eu.orders")

        self.assertNotEqual(left.resource_key, right.resource_key)
        self.assertEqual(len(left.resource_key), 64)
        self.assertEqual(
            left.resource_key,
            AssetIdentity("doris", "sales.eu", "orders").resource_key,
        )


class AuthorizationServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证角色授权策略构建"""

    async def test_admin_receives_unrestricted_policy(self) -> None:
        repo = MagicMock(spec=AuthPGRepo)
        repo.get_user_by_id = AsyncMock(return_value=build_user(PlatformRole.ADMIN))
        repo.list_asset_grants_for_roles = AsyncMock()

        policy = await AuthorizationService(repo).get_asset_policy(8)

        self.assertTrue(policy.unrestricted)
        self.assertTrue(
            policy.allows(AssetIdentity("any-source", "any-db", "any-table"))
        )
        repo.list_asset_grants_for_roles.assert_not_awaited()

    async def test_analyst_policy_is_union_of_role_grants(self) -> None:
        repo = MagicMock(spec=AuthPGRepo)
        user = build_user(PlatformRole.ANALYST)
        grant = RoleAssetGrant(
            role_name=PlatformRole.ANALYST.value,
            scope="column",
            data_source="doris",
            database_name="sales",
            table_name="orders",
            column_name="amount",
            resource_key="stored-key",
        )
        repo.get_user_by_id = AsyncMock(return_value=user)
        repo.list_asset_grants_for_roles = AsyncMock(return_value=[grant])

        policy = await AuthorizationService(repo).get_asset_policy(user.id)

        self.assertTrue(
            policy.allows(AssetIdentity("doris", "sales", "orders", "amount"))
        )
        self.assertFalse(
            policy.allows(AssetIdentity("doris", "sales", "orders", "cost"))
        )


class RoleManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证管理员角色安全边界"""

    async def test_last_admin_role_cannot_be_removed(self) -> None:
        repo = MagicMock(spec=AuthPGRepo)
        repo.transaction.return_value = AsyncTransactionStub()
        repo.lock_user_provisioning = AsyncMock()
        repo.ensure_base_roles = AsyncMock()
        repo.get_user_by_id = AsyncMock(return_value=build_user(PlatformRole.ADMIN))
        repo.count_users_with_role = AsyncMock(return_value=1)
        repo.set_user_roles = AsyncMock()
        repo.revoke_user_refresh_tokens = AsyncMock()

        with self.assertRaises(auth_error.LastAdminRoleError):
            await RoleManagementService(repo).set_user_roles(
                8,
                {PlatformRole.ANALYST},
            )

        repo.set_user_roles.assert_not_awaited()
        repo.revoke_user_refresh_tokens.assert_not_awaited()
