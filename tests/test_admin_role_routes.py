import unittest
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr, ValidationError

from app.models.auth import DorisQueryIdentity
from app.routes.api.v1.admin import schemas
from app.routes.api.v1.admin.router import (
    attach_doris_role,
    create_doris_role,
    create_user,
    delete_user,
    discover_doris_roles,
    list_doris_roles,
    list_users,
    set_user_administrator,
    set_user_doris_role,
)
from app.services.authorization_service import DorisDiscoveredRole
from app.services.doris_permission_service import DorisRoleStatus
from tests.test_auth_service import build_user


class AdminRoleRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_role_never_returns_generated_password(self) -> None:
        service = MagicMock()
        service.create_role = AsyncMock(
            return_value=DorisQueryIdentity(
                role_name="sales",
                description="Sales",
                query_user="sales_query",
                encrypted_password="secret-ciphertext",
                workload_group="normal",
                is_default=True,
                is_active=True,
            )
        )

        response = await create_doris_role(
            schemas.CreateDorisRoleRequest(
                role="sales",
                description="Sales",
                query_user="sales_query",
                workload_group="normal",
                is_default=True,
            ),
            MagicMock(),
            service,
        )

        self.assertEqual(response.query_user, "sales_query")
        self.assertNotIn("password", response.model_dump())

    async def test_discover_roles_endpoint(self) -> None:
        service = MagicMock()
        service.discover_roles = AsyncMock(
            return_value=[
                DorisDiscoveredRole(
                    name="finance",
                    is_attached=False,
                )
            ]
        )

        response = await discover_doris_roles(MagicMock(), service)

        self.assertEqual(len(response.roles), 1)
        self.assertEqual(response.roles[0].name, "finance")
        self.assertFalse(response.roles[0].is_attached)

    async def test_attach_role_endpoint(self) -> None:
        service = MagicMock()
        service.attach_role = AsyncMock(
            return_value=DorisQueryIdentity(
                role_name="finance",
                description="Finance Role",
                query_user="finance_query_user",
                encrypted_password="enc-pwd",
                workload_group="normal",
                is_default=False,
                is_active=True,
            )
        )

        response = await attach_doris_role(
            schemas.AttachDorisRoleRequest(
                role="finance",
                description="Finance Role",
            ),
            MagicMock(),
            service,
        )

        self.assertEqual(response.name, "finance")
        self.assertEqual(response.query_user, "finance_query_user")
        self.assertNotIn("password", response.model_dump())

    async def test_roles_are_read_from_doris_status(self) -> None:
        service = MagicMock()
        service.list_roles = AsyncMock(
            return_value=[
                DorisRoleStatus(
                    name="dataagent_default",
                    description="缺省角色",
                    is_default=True,
                    is_active=True,
                    query_user="default_query",
                    workload_group="normal",
                    exists_in_doris=True,
                    doris_grants={"TablePrivs": "internal.ecommerce.orders"},
                )
            ]
        )

        response = await list_doris_roles(MagicMock(), service)

        self.assertEqual(response.roles[0].name, "dataagent_default")
        self.assertTrue(response.roles[0].exists_in_doris)

    async def test_user_receives_one_doris_role(self) -> None:
        service = MagicMock()
        user = build_user(doris_role="sales")
        service.set_user_doris_role = AsyncMock(return_value=user)

        response = await set_user_doris_role(
            user.id,
            schemas.SetUserDorisRoleRequest(role="sales"),
            MagicMock(),
            service,
        )

        self.assertEqual(response.doris_role, "sales")
        service.set_user_doris_role.assert_awaited_once_with(user.id, "sales")

    async def test_administrator_flag_is_separate_from_doris_role(self) -> None:
        service = MagicMock()
        user = build_user(is_admin=True, doris_role="sales")
        service.set_user_admin = AsyncMock(return_value=user)

        response = await set_user_administrator(
            user.id,
            schemas.SetUserAdministratorRequest(is_admin=True),
            MagicMock(),
            service,
        )

        self.assertTrue(response.is_admin)
        self.assertEqual(response.doris_role, "sales")

    def test_multiple_roles_cannot_be_submitted(self) -> None:
        with self.assertRaises(ValidationError):
            schemas.SetUserDorisRoleRequest.model_validate(
                {"role": ["sales", "finance"]}
            )

    async def test_create_user_endpoint(self) -> None:
        service = MagicMock()
        user = build_user(user_id=12, doris_role="sales")
        service.create_user = AsyncMock(return_value=user)

        response = await create_user(
            schemas.CreateUserRequest(
                username="new_user",
                email="new@example.com",
                password=SecretStr("password123"),
                doris_role="sales",
                is_admin=False,
            ),
            MagicMock(),
            service,
        )

        self.assertEqual(response.username, "analyst")
        self.assertEqual(response.doris_role, "sales")
        service.create_user.assert_awaited_once_with(
            username="new_user",
            email="new@example.com",
            password="password123",
            doris_role="sales",
            is_admin=False,
        )

    async def test_delete_user_endpoint(self) -> None:
        service = MagicMock()
        service.request_deletion = AsyncMock()
        admin_user = build_user(user_id=1, is_admin=True)

        response = await delete_user(
            12,
            admin_user,
            service,
        )

        self.assertEqual(response.status_code, 204)
        service.request_deletion.assert_awaited_once_with(12, operator_id=1)

    async def test_list_users_returns_page_metadata(self) -> None:
        service = MagicMock()
        service.list_users = AsyncMock(return_value=([build_user(user_id=51)], 101))

        response = await list_users(
            MagicMock(),
            service,
            limit=50,
            offset=50,
        )

        self.assertEqual(len(response.users), 1)
        self.assertEqual(response.total, 101)
        self.assertEqual(response.limit, 50)
        self.assertEqual(response.offset, 50)
        self.assertTrue(response.has_more)
        service.list_users.assert_awaited_once_with(limit=50, offset=50)


if __name__ == "__main__":
    unittest.main()
