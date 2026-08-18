import unittest
from unittest.mock import AsyncMock, MagicMock

from pydantic import ValidationError

from app.routes.api.v1.admin import schemas
from app.routes.api.v1.admin.router import (
    list_doris_roles,
    set_user_administrator,
    set_user_doris_role,
)
from app.services.doris_permission_service import DorisRoleStatus
from tests.test_auth_service import build_user


class AdminRoleRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_roles_are_read_from_doris_status(self) -> None:
        service = MagicMock()
        service.list_roles = AsyncMock(
            return_value=[
                DorisRoleStatus(
                    name="dataagent_default",
                    description="缺省角色",
                    is_default=True,
                    query_user="default_query",
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


if __name__ == "__main__":
    unittest.main()
