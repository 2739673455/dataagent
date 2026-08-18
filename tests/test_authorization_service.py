"""Doris 单角色授权策略测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.conf.app_config import DorisRoleConfig
from app.entities.auth import DorisRoleAssetGrant
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.services.authorization_service import (
    AssetAccessPolicy,
    AssetIdentity,
    AuthorizationService,
    DorisRoleManagementService,
)
from tests.test_auth_service import AsyncTransactionStub, build_user


def role_config(*, default: bool = False) -> DorisRoleConfig:
    """构造 Doris 角色配置"""
    return DorisRoleConfig(
        description="测试角色",
        is_default=default,
        query_user="query_user",
        query_password="secret",
        workload_group="normal",
    )


class AssetAccessPolicyTest(unittest.TestCase):
    """验证层级授权的访问与目录可见语义"""

    def test_column_grant_makes_table_visible_without_allowing_whole_table(
        self,
    ) -> None:
        table = AssetIdentity("doris", "sales", "orders")
        amount = AssetIdentity("doris", "sales", "orders", "amount")
        secret = AssetIdentity("doris", "sales", "orders", "secret_note")
        policy = AssetAccessPolicy(user_id=1, grants=frozenset({amount}))

        self.assertTrue(policy.is_visible(table))
        self.assertFalse(policy.allows(table))
        self.assertTrue(policy.allows(amount))
        self.assertFalse(policy.allows(secret))

    def test_database_grant_allows_descendants(self) -> None:
        policy = AssetAccessPolicy(
            user_id=1,
            grants=frozenset({AssetIdentity("doris", "sales")}),
        )

        self.assertTrue(policy.allows(AssetIdentity("doris", "sales", "orders")))


class AuthorizationServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证唯一 Doris 角色授权策略构建"""

    async def test_admin_still_uses_assigned_doris_role_policy(self) -> None:
        repo = MagicMock(spec=AuthPGRepo)
        user = build_user(is_admin=True, doris_role="sales")
        grant = DorisRoleAssetGrant(
            role_name="sales",
            scope="column",
            data_source="doris",
            database_name="sales",
            table_name="orders",
            column_name="amount",
            resource_key="stored-key",
        )
        repo.get_user_by_id = AsyncMock(return_value=user)
        repo.list_role_asset_grants = AsyncMock(return_value=[grant])

        policy = await AuthorizationService(repo).get_asset_policy(user.id)

        self.assertFalse(policy.unrestricted)
        self.assertTrue(
            policy.allows(AssetIdentity("doris", "sales", "orders", "amount"))
        )
        self.assertFalse(
            policy.allows(AssetIdentity("doris", "sales", "orders", "cost"))
        )

    async def test_non_admin_is_rejected_from_admin_operations(self) -> None:
        with self.assertRaises(auth_error.PermissionDeniedError):
            AuthorizationService.require_admin(build_user())


class DorisRoleManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证用户唯一角色与管理员安全边界"""

    def setUp(self) -> None:
        self.repo = MagicMock(spec=AuthPGRepo)
        self.repo.transaction.return_value = AsyncTransactionStub()
        self.repo.lock_security_mutation = AsyncMock()
        self.repo.revoke_user_refresh_tokens = AsyncMock()
        self.roles = {
            "dataagent_default": role_config(default=True),
            "sales": role_config(),
        }

    async def test_user_role_is_replaced_with_one_configured_role(self) -> None:
        user = build_user(doris_role="dataagent_default")
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.repo.set_user_doris_role = AsyncMock(
            side_effect=lambda target, role: setattr(target, "doris_role_name", role)
        )
        service = DorisRoleManagementService(self.repo, self.roles)

        updated = await service.set_user_doris_role(user.id, "sales")

        self.assertEqual(updated.doris_role_name, "sales")
        self.repo.set_user_doris_role.assert_awaited_once_with(user, "sales")
        self.repo.revoke_user_refresh_tokens.assert_awaited_once()

    async def test_unknown_doris_role_is_rejected(self) -> None:
        service = DorisRoleManagementService(self.repo, self.roles)

        with self.assertRaises(auth_error.RoleNotFoundError):
            await service.set_user_doris_role(7, "unknown")

    async def test_last_administrator_cannot_be_removed(self) -> None:
        admin = build_user(is_admin=True)
        self.repo.get_user_by_id = AsyncMock(return_value=admin)
        self.repo.count_admins = AsyncMock(return_value=1)
        self.repo.set_user_admin = AsyncMock()
        service = DorisRoleManagementService(self.repo, self.roles)

        with self.assertRaises(auth_error.LastAdministratorError):
            await service.set_user_admin(admin.id, False)

        self.repo.set_user_admin.assert_not_awaited()
