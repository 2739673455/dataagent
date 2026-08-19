"""Doris 单角色授权策略测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.clients.doris_client_manager import DorisQueryClientRegistry
from app.errors import auth_error
from app.models.auth import DorisQueryIdentity, DorisRoleAssetGrant
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.doris_query_identity_pg_repo import DorisQueryIdentityPGRepo
from app.repositories.doris_role_repo import DorisRoleRepository
from app.services.authorization_service import (
    AssetAccessPolicy,
    AssetIdentity,
    AuthorizationService,
    DorisRoleManagementService,
)
from tests.test_auth_service import AsyncTransactionStub, build_user


def query_identity(
    role: str = "sales",
    *,
    default: bool = False,
) -> DorisQueryIdentity:
    """构造 Doris 查询身份"""
    return DorisQueryIdentity(
        role_name=role,
        description="测试角色",
        is_default=default,
        is_active=True,
        query_user=f"{role}_query",
        encrypted_password="encrypted",
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

    async def test_platform_admin_without_data_role_cannot_run_analysis(self) -> None:
        with self.assertRaises(auth_error.PermissionDeniedError):
            AuthorizationService.require_analysis_access(
                build_user(is_admin=True, doris_role=None),
                None,
            )


class DorisRoleManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证用户唯一角色与管理员安全边界"""

    def setUp(self) -> None:
        self.repo = MagicMock(spec=AuthPGRepo)
        self.repo.transaction.return_value = AsyncTransactionStub()
        self.repo.lock_security_mutation = AsyncMock()
        self.repo.revoke_user_refresh_tokens = AsyncMock()
        self.identity_repo = MagicMock(spec=DorisQueryIdentityPGRepo)
        self.identity_repo.get = AsyncMock(return_value=query_identity())
        self.doris_repo = MagicMock(spec=DorisRoleRepository)
        self.cipher = MagicMock()
        self.registry = MagicMock(spec=DorisQueryClientRegistry)

    def service(self) -> DorisRoleManagementService:
        return DorisRoleManagementService(
            self.repo,
            self.identity_repo,
            self.doris_repo,
            self.cipher,
            self.registry,
        )

    async def test_user_role_is_replaced_with_one_configured_role(self) -> None:
        user = build_user(doris_role="dataagent_default")
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.repo.set_user_doris_role = AsyncMock(
            side_effect=lambda target, role: setattr(target, "doris_role_name", role)
        )
        service = self.service()

        updated = await service.set_user_doris_role(user.id, "sales")

        self.assertEqual(updated.doris_role_name, "sales")
        self.repo.set_user_doris_role.assert_awaited_once_with(user, "sales")
        self.repo.revoke_user_refresh_tokens.assert_awaited_once()

    async def test_unknown_doris_role_is_rejected(self) -> None:
        self.identity_repo.get.return_value = None
        service = self.service()

        with self.assertRaises(auth_error.RoleNotFoundError):
            await service.set_user_doris_role(7, "unknown")

    async def test_last_administrator_cannot_be_removed(self) -> None:
        admin = build_user(is_admin=True)
        self.repo.get_user_by_id = AsyncMock(return_value=admin)
        self.repo.count_admins = AsyncMock(return_value=1)
        self.repo.set_user_admin = AsyncMock()
        service = self.service()

        with self.assertRaises(auth_error.LastAdministratorError):
            await service.set_user_admin(admin.id, False)

        self.repo.set_user_admin.assert_not_awaited()

    async def test_first_dynamic_role_becomes_default_and_password_is_encrypted(
        self,
    ) -> None:
        self.identity_repo.get.return_value = None
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
        self.identity_repo.get_default = AsyncMock(return_value=None)
        self.identity_repo.clear_default = AsyncMock()
        self.identity_repo.add = AsyncMock(side_effect=lambda identity: identity)
        self.doris_repo.quote_identifier.return_value = "quoted"
        self.doris_repo.create_role_identity = AsyncMock()
        self.cipher.generate_password.return_value = "generated-password"
        self.cipher.encrypt.return_value = "encrypted-password"

        identity = await self.service().create_role(
            role_name="sales",
            description="Sales analysts",
            query_user="sales_query",
            workload_group="normal",
            is_default=False,
        )

        self.assertTrue(identity.is_default)
        self.assertEqual(identity.encrypted_password, "encrypted-password")
        self.doris_repo.create_role_identity.assert_awaited_once_with(
            role_name="sales",
            query_user="sales_query",
            password="generated-password",
            workload_group="normal",
        )

    async def test_default_or_assigned_role_cannot_be_deleted(self) -> None:
        self.identity_repo.get.return_value = query_identity(default=True)

        with self.assertRaises(auth_error.DefaultRoleRequiredError):
            await self.service().delete_role("sales")

        self.identity_repo.get.return_value = query_identity(default=False)
        self.identity_repo.count_assigned_users = AsyncMock(return_value=1)
        with self.assertRaises(auth_error.RoleInUseError):
            await self.service().delete_role("sales")

    async def test_discover_roles_distinguishes_attached_roles(self) -> None:
        self.doris_repo.list_roles = AsyncMock(
            return_value=[
                {"Role": "sales"},
                {"Role": "finance"},
                {"Role": "admin"},
            ]
        )
        self.identity_repo.list_all = AsyncMock(
            return_value=[query_identity(role="sales")]
        )

        discovered = await self.service().discover_roles()

        self.assertEqual(len(discovered), 2)
        sales = next(d for d in discovered if d.name == "sales")
        finance = next(d for d in discovered if d.name == "finance")
        self.assertTrue(sales.is_attached)
        self.assertFalse(finance.is_attached)

    async def test_attach_role_creates_query_user_and_persists_identity(self) -> None:
        self.identity_repo.get = AsyncMock(return_value=None)
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
        self.identity_repo.get_default = AsyncMock(return_value=query_identity(default=True))
        self.identity_repo.add = AsyncMock(side_effect=lambda identity: identity)
        self.doris_repo.quote_identifier.return_value = "quoted"
        self.doris_repo.verify_configured_roles = AsyncMock()
        self.doris_repo.create_query_user_for_existing_role = AsyncMock()
        self.cipher.generate_password.return_value = "gen-pwd"
        self.cipher.encrypt.return_value = "enc-pwd"

        identity = await self.service().attach_role(
            role_name="finance",
            description="Finance Role",
            workload_group="normal",
            query_user="finance_custom_query",
            is_default=False,
        )

        self.assertEqual(identity.role_name, "finance")
        self.assertEqual(identity.query_user, "finance_custom_query")
        self.assertEqual(identity.encrypted_password, "enc-pwd")
        self.doris_repo.verify_configured_roles.assert_awaited_once_with(("finance",))
        self.doris_repo.create_query_user_for_existing_role.assert_awaited_once_with(
            role_name="finance",
            query_user="finance_custom_query",
            password="gen-pwd",
            workload_group="normal",
        )
