import unittest
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr, ValidationError

from app.identity.api.admin import schemas
from app.identity.api.admin.router import (
    clear_default_doris_role,
    create_doris_role,
    create_user,
    delete_user,
    list_doris_roles,
    list_doris_workload_groups,
    list_existing_doris_roles,
    list_row_policies,
    list_users,
    set_user_administrator,
    set_user_doris_role,
    update_user,
)
from app.identity.models.doris import DorisQueryIdentity, DorisRowPolicy
from app.identity.services.auth import AuthenticatedUser
from app.identity.services.doris_permission import DorisRoleStatus
from tests.identity.test_auth_service import build_user


class AdminRoleRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_row_policies_returns_structured_response(self) -> None:
        service = MagicMock()
        service.list_row_policies = AsyncMock(
            return_value=[
                DorisRowPolicy(
                    policy_name="region_east_filter",
                    catalog_name="internal",
                    database_name="ecommerce",
                    table_name="orders",
                    policy_type="RESTRICTIVE",
                    predicate="region = 'east'",
                )
            ]
        )

        response = await list_row_policies("sales", MagicMock(), service)

        self.assertEqual(
            response.policies[0].model_dump(),
            {
                "policy_name": "region_east_filter",
                "catalog_name": "internal",
                "database_name": "ecommerce",
                "table_name": "orders",
                "policy_type": "RESTRICTIVE",
                "predicate": "region = 'east'",
            },
        )

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
            )
        )

        response = await create_doris_role(
            schemas.CreateDorisRoleRequest(
                role="sales",
                description="Sales",
                query_user="sales_query",
                workload_group="normal",
            ),
            MagicMock(),
            service,
        )

        self.assertEqual(response.query_user, "sales_query")
        self.assertNotIn("password", response.model_dump())

    async def test_list_workload_groups_endpoint(self) -> None:
        service = MagicMock()
        service.list_workload_groups = AsyncMock(return_value=("batch", "normal"))

        response = await list_doris_workload_groups(MagicMock(), service)

        self.assertEqual(response.workload_groups, ["batch", "normal"])

    async def test_list_existing_roles_endpoint(self) -> None:
        service = MagicMock()
        existing_role = MagicMock(name="existing_role")
        existing_role.name = "operator"
        existing_role.managed = False
        existing_role.doris_users = ("'root'@'%'",)
        managed_role = MagicMock(name="managed_role")
        managed_role.name = "sales"
        managed_role.managed = True
        managed_role.doris_users = ("'sales_query'@'%'",)
        service.list_existing_roles = AsyncMock(
            return_value=[existing_role, managed_role]
        )

        response = await list_existing_doris_roles(MagicMock(), service)

        self.assertEqual(
            [role.model_dump() for role in response.roles],
            [
                {
                    "name": "operator",
                    "managed": False,
                    "doris_users": ["'root'@'%'"],
                },
                {
                    "name": "sales",
                    "managed": True,
                    "doris_users": ["'sales_query'@'%'"],
                },
            ],
        )

    async def test_clear_default_role_endpoint(self) -> None:
        service = MagicMock()
        service.clear_default_role = AsyncMock()

        response = await clear_default_doris_role(
            MagicMock(),
            service,
        )

        self.assertEqual(response.status_code, 204)
        service.clear_default_role.assert_awaited_once_with()

    async def test_roles_are_read_from_doris_status(self) -> None:
        service = MagicMock()
        service.list_roles = AsyncMock(
            return_value=[
                DorisRoleStatus(
                    name="dataagent_default",
                    description="缺省角色",
                    is_default=True,
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

    def test_create_role_rejects_workload_group_unsupported_by_query_runtime(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            schemas.CreateDorisRoleRequest(
                role="sales",
                description="Sales",
                query_user="sales_query",
                workload_group="group$name",
            )

    def test_update_user_requires_a_non_null_update(self) -> None:
        invalid_payloads = ({}, {"username": None}, {"is_admin": None})
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                schemas.UpdateUserRequest.model_validate(payload)

    def test_update_user_allows_null_to_clear_doris_role(self) -> None:
        request = schemas.UpdateUserRequest(doris_role=None)

        self.assertEqual(request.model_fields_set, {"doris_role"})
        self.assertIsNone(request.doris_role)

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
        service.request_deletion = AsyncMock(return_value=True)
        admin_user = build_user(user_id=1, is_admin=True)

        response = await delete_user(
            12,
            AuthenticatedUser.from_user(admin_user),
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
            query="test_query",
        )

        self.assertEqual(len(response.users), 1)
        self.assertEqual(response.total, 101)
        self.assertEqual(response.limit, 50)
        self.assertEqual(response.offset, 50)
        self.assertTrue(response.has_more)
        service.list_users.assert_awaited_once_with(
            limit=50, offset=50, query="test_query"
        )

    async def test_update_user_endpoint(self) -> None:
        service = MagicMock()
        user = build_user(user_id=12)
        service.update_user = AsyncMock(return_value=user)

        response = await update_user(
            12,
            schemas.UpdateUserRequest(
                username="new_name",
                email="new_email@example.com",
                password=SecretStr("new_password_123"),
            ),
            MagicMock(),
            service,
        )

        self.assertEqual(response.id, 12)
        service.update_user.assert_awaited_once_with(
            12,
            username="new_name",
            email="new_email@example.com",
            password="new_password_123",
        )

    async def test_update_user_endpoint_preserves_explicit_role_clear(self) -> None:
        service = MagicMock()
        service.update_user = AsyncMock(return_value=build_user(user_id=12))

        await update_user(
            12,
            schemas.UpdateUserRequest(doris_role=None),
            MagicMock(),
            service,
        )

        service.update_user.assert_awaited_once_with(
            12,
            doris_role=None,
            update_doris_role=True,
        )


if __name__ == "__main__":
    unittest.main()
