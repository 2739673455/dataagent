"""Doris 单角色授权策略测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.identity import errors as auth_error
from app.identity.models.doris import DorisQueryIdentity, DorisRoleAssetGrant
from app.identity.repositories.auth import AuthPGRepo
from app.identity.repositories.doris_role import (
    DorisQueryUserAlreadyExistsError,
    DorisRoleAlreadyExistsError,
    DorisRoleRepository,
    DorisWorkloadGroupNotFoundError,
)
from app.identity.repositories.query_identity import DorisQueryIdentityPGRepo
from app.identity.services.auth import AuthenticatedUser
from app.identity.services.authorization import (
    AssetAccessPolicy,
    AssetIdentity,
    AuthorizationService,
    DorisRoleManagementService,
)
from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from tests.identity.test_auth_service import AsyncSessionStub, build_config, build_user


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
        repo.session = MagicMock()
        identity = query_identity("sales")
        repo.session.scalar = AsyncMock(return_value=identity)

        policy = await AuthorizationService(repo).get_asset_policy(user.id)

        self.assertTrue(
            policy.allows(AssetIdentity("doris", "sales", "orders", "amount"))
        )
        self.assertFalse(
            policy.allows(AssetIdentity("doris", "sales", "orders", "cost"))
        )
        self.assertEqual(policy.role_name, "sales")
        self.assertEqual(policy.authorization_epoch, identity.authorization_epoch)

    async def test_non_admin_is_rejected_from_admin_operations(self) -> None:
        with self.assertRaises(auth_error.PermissionDeniedError):
            AuthorizationService.require_admin(
                AuthenticatedUser.from_user(build_user())
            )

    async def test_platform_admin_without_data_role_cannot_run_analysis(self) -> None:
        with self.assertRaises(auth_error.PermissionDeniedError):
            AuthorizationService.require_analysis_access(
                AuthenticatedUser.from_user(build_user(is_admin=True, doris_role=None)),
                None,
            )


class DorisRoleManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证用户唯一角色与管理员安全边界"""

    def setUp(self) -> None:
        self.repo = MagicMock(spec=AuthPGRepo)
        self.session = AsyncSessionStub()
        self.repo.session = self.session
        self.repo.lock_security_mutation = AsyncMock()
        self.repo.revoke_user_refresh_tokens = AsyncMock()
        self.identity_repo = MagicMock(spec=DorisQueryIdentityPGRepo)
        self.identity_repo.session = self.session
        self.identity_repo.get = AsyncMock(return_value=query_identity())
        self.doris_repo = MagicMock(spec=DorisRoleRepository)
        self.doris_repo.list_workload_groups = AsyncMock(return_value=("normal",))
        self.doris_repo.workload_group_exists = AsyncMock(return_value=True)
        self.cipher = MagicMock()
        self.registry = MagicMock(spec=DorisQueryClientRegistry)
        self.password_manager = MagicMock()
        self.password_manager.hash = AsyncMock(return_value="hashed-password")

    def service(self) -> DorisRoleManagementService:
        return DorisRoleManagementService(
            self.repo,
            self.identity_repo,
            self.doris_repo,
            self.cipher,
            self.registry,
            self.password_manager,
            build_config(),
        )

    def test_rejects_postgres_repositories_from_different_sessions(self) -> None:
        self.identity_repo.session = AsyncSessionStub()

        with self.assertRaisesRegex(ValueError, "必须共享同一数据库会话"):
            self.service()

    async def test_lists_workload_groups_from_doris(self) -> None:
        workload_groups = await self.service().list_workload_groups()

        self.assertEqual(workload_groups, ("normal",))
        self.doris_repo.list_workload_groups.assert_awaited_once_with()

    async def test_lists_existing_doris_roles_with_management_status(self) -> None:
        self.doris_repo.list_roles = AsyncMock(
            return_value=[
                {"Name": "sales", "Users": "'sales_query'@'%'"},
                {"Name": "operator", "Users": "'root'@'%'"},
            ]
        )
        self.identity_repo.list_all = AsyncMock(return_value=[query_identity("sales")])

        roles = await self.service().list_existing_roles()

        self.assertEqual(
            [(role.name, role.managed, role.doris_users) for role in roles],
            [
                ("operator", False, ("'root'@'%'",)),
                ("sales", True, ("'sales_query'@'%'",)),
            ],
        )

    async def test_list_users_returns_rows_and_total(self) -> None:
        users = [build_user(user_id=51)]
        self.repo.list_users = AsyncMock(return_value=users)
        self.repo.count_users = AsyncMock(return_value=101)

        page_users, total = await self.service().list_users(
            limit=50, offset=50, query=" alice "
        )

        self.assertEqual(page_users, users)
        self.assertEqual(total, 101)
        self.repo.list_users.assert_awaited_once_with(
            limit=50, offset=50, query="alice"
        )
        self.repo.count_users.assert_awaited_once_with(query="alice")

    async def test_user_role_is_replaced_with_one_configured_role(self) -> None:
        user = build_user(doris_role="dataagent_default")
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.identity_repo.get = AsyncMock(
            side_effect=self._load_identity_in_transaction
        )
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

    async def test_created_role_stays_non_default_and_password_is_encrypted(
        self,
    ) -> None:
        self.identity_repo.get.return_value = None
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
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
        )

        self.assertFalse(identity.is_default)
        self.assertEqual(identity.encrypted_password, "encrypted-password")
        self.doris_repo.create_role_identity.assert_awaited_once_with(
            role_name="sales",
            query_user="sales_query",
            password="generated-password",
            workload_group="normal",
        )
        self.doris_repo.workload_group_exists.assert_awaited_once_with("normal")

    async def test_missing_workload_group_is_rejected_before_role_creation(
        self,
    ) -> None:
        self.doris_repo.workload_group_exists.return_value = False

        with self.assertRaises(auth_error.WorkloadGroupNotFoundError) as context:
            await self.service().create_role(
                role_name="sales",
                description="Sales analysts",
                query_user="sales_query",
                workload_group="missing",
            )

        self.assertEqual(
            context.exception.detail,
            "Doris 工作组 missing 不存在，请选择已创建的工作组",
        )
        self.doris_repo.create_role_identity.assert_not_awaited()

    async def test_workload_group_deleted_during_creation_is_mapped(self) -> None:
        self.identity_repo.get.return_value = None
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
        self.doris_repo.create_role_identity = AsyncMock(
            side_effect=DorisWorkloadGroupNotFoundError("batch")
        )
        self.cipher.generate_password.return_value = "generated-password"

        with self.assertRaises(auth_error.WorkloadGroupNotFoundError) as context:
            await self.service().create_role(
                role_name="sales",
                description="Sales analysts",
                query_user="sales_query",
                workload_group="batch",
            )

        self.assertEqual(
            context.exception.detail,
            "Doris 工作组 batch 不存在，请选择已创建的工作组",
        )

    async def test_existing_doris_role_is_mapped_to_named_conflict(self) -> None:
        self.identity_repo.get.return_value = None
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
        self.doris_repo.create_role_identity = AsyncMock(
            side_effect=DorisRoleAlreadyExistsError("sales")
        )
        self.cipher.generate_password.return_value = "generated-password"

        with self.assertRaises(auth_error.RoleAlreadyExistsError) as context:
            await self.service().create_role(
                role_name="sales",
                description="Sales analysts",
                query_user="sales_query",
                workload_group="normal",
            )

        self.assertEqual(context.exception.detail, "Doris 角色 sales 已存在")

    async def test_existing_doris_query_user_is_mapped_to_user_conflict(self) -> None:
        self.identity_repo.get.return_value = None
        self.identity_repo.get_by_query_user = AsyncMock(return_value=None)
        self.doris_repo.create_role_identity = AsyncMock(
            side_effect=DorisQueryUserAlreadyExistsError("sales_query")
        )
        self.cipher.generate_password.return_value = "generated-password"

        with self.assertRaises(auth_error.QueryUserAlreadyExistsError) as context:
            await self.service().create_role(
                role_name="sales",
                description="Sales analysts",
                query_user="sales_query",
                workload_group="normal",
            )

        self.assertEqual(
            context.exception.detail,
            "Doris 查询用户 sales_query 已存在",
        )

    async def test_default_role_can_be_deleted_when_unassigned(self) -> None:
        self.identity_repo.get.return_value = query_identity(default=True)
        self.identity_repo.count_assigned_users = AsyncMock(return_value=0)
        self.identity_repo.delete = AsyncMock()
        self.repo.delete_role_asset_grants = AsyncMock()
        self.doris_repo.drop_role_identity = AsyncMock()
        self.registry.invalidate = AsyncMock()

        await self.service().delete_role("sales")

        self.doris_repo.drop_role_identity.assert_awaited_once_with(
            role_name="sales",
            query_user="sales_query",
        )
        self.identity_repo.delete.assert_awaited_once()

    async def test_assigned_role_cannot_be_deleted(self) -> None:
        self.identity_repo.get.return_value = query_identity(default=True)
        self.identity_repo.count_assigned_users = AsyncMock(return_value=1)

        with self.assertRaises(auth_error.RoleInUseError):
            await self.service().delete_role("sales")

    async def test_default_role_can_be_cleared(self) -> None:
        self.identity_repo.clear_default = AsyncMock()

        await self.service().clear_default_role()

        self.identity_repo.clear_default.assert_awaited_once_with()

    async def test_create_user_persists_user_with_assigned_role(self) -> None:
        self.repo.get_user_by_username = AsyncMock(return_value=None)
        self.repo.get_user_by_email = AsyncMock(return_value=None)
        self.repo.add_user = AsyncMock(side_effect=lambda u: u)
        self.identity_repo.get = AsyncMock(
            side_effect=self._load_identity_in_transaction
        )

        user = await self.service().create_user(
            username=" NEW_OPERATOR ",
            email=" Operator@Example.COM ",
            password="password123",
            doris_role="sales",
            is_admin=False,
        )

        self.assertEqual(user.username, "new_operator")
        self.assertEqual(user.email, "operator@example.com")
        self.assertEqual(user.doris_role_name, "sales")
        self.assertFalse(user.is_admin)
        self.repo.add_user.assert_awaited_once()

    async def test_create_user_rejects_invalid_normalized_identity(self) -> None:
        for username, email in (
            ("invalid user", "operator@example.com"),
            ("new_operator", "invalid-email"),
        ):
            with (
                self.subTest(username=username, email=email),
                self.assertRaises(auth_error.InvalidUserMutationError),
            ):
                await self.service().create_user(
                    username=username,
                    email=email,
                    password="password123",
                )

        self.password_manager.hash.assert_not_awaited()

    async def test_create_user_maps_password_policy_failures_consistently(
        self,
    ) -> None:
        for password in ("short", "x" * 129):
            with (
                self.subTest(password_length=len(password)),
                self.assertRaises(auth_error.WeakPasswordError),
            ):
                await self.service().create_user(
                    username="new_operator",
                    email="operator@example.com",
                    password=password,
                )

        self.password_manager.hash.assert_not_awaited()

    async def test_create_user_stays_unassigned_without_default_role(self) -> None:
        self.repo.get_user_by_username = AsyncMock(return_value=None)
        self.repo.get_user_by_email = AsyncMock(return_value=None)
        self.repo.add_user = AsyncMock(side_effect=lambda user: user)
        self.identity_repo.get_default = AsyncMock(return_value=None)

        user = await self.service().create_user(
            username="new_operator",
            email="operator@example.com",
            password="password123",
        )

        self.assertIsNone(user.doris_role_name)

    async def _load_identity_in_transaction(
        self,
        role_name: str,
    ) -> DorisQueryIdentity:
        self.assertTrue(self.session.active)
        return query_identity(role=role_name)

    async def test_create_user_rejects_duplicate_username(self) -> None:
        self.repo.get_user_by_username = AsyncMock(return_value=build_user())
        service = self.service()

        with self.assertRaises(auth_error.UsernameAlreadyExistsError):
            await service.create_user(
                username="analyst",
                email="diff@example.com",
                password="password123",
            )

    async def test_update_user_updates_profile_and_password_and_revokes_tokens(
        self,
    ) -> None:
        user = build_user(user_id=15)
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.repo.get_user_by_username = AsyncMock(return_value=None)
        self.repo.get_user_by_email = AsyncMock(return_value=None)
        self.repo.update_user = AsyncMock()
        self.repo.revoke_user_refresh_tokens = AsyncMock()

        updated = await self.service().update_user(
            user_id=15,
            username="updated_user",
            email="updated@example.com",
            password="new_secret_password",
        )

        self.assertEqual(updated.id, 15)
        self.repo.update_user.assert_awaited_once()
        self.repo.revoke_user_refresh_tokens.assert_awaited_once()

    async def test_update_user_validates_doris_role_inside_transaction(self) -> None:
        user = build_user(user_id=15, doris_role="dataagent_default")
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.repo.update_user = AsyncMock()
        self.identity_repo.get = AsyncMock(
            side_effect=self._load_identity_in_transaction
        )

        updated = await self.service().update_user(
            user_id=user.id,
            doris_role="sales",
            update_doris_role=True,
        )

        self.assertEqual(updated.id, user.id)
        self.identity_repo.get.assert_awaited_once_with("sales")
        self.repo.update_user.assert_awaited_once_with(
            user,
            username=None,
            email=None,
            password_hash=None,
            doris_role="sales",
            update_doris_role=True,
            is_admin=None,
        )

    async def test_update_user_can_explicitly_clear_doris_role(self) -> None:
        user = build_user(user_id=15, doris_role="sales")
        self.repo.get_user_by_id = AsyncMock(return_value=user)
        self.repo.update_user = AsyncMock()

        await self.service().update_user(
            user_id=user.id,
            doris_role=None,
            update_doris_role=True,
        )

        self.identity_repo.get.assert_not_awaited()
        self.repo.update_user.assert_awaited_once_with(
            user,
            username=None,
            email=None,
            password_hash=None,
            doris_role=None,
            update_doris_role=True,
            is_admin=None,
        )
