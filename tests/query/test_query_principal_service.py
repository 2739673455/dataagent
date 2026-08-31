"""Doris 单角色查询身份解析测试。"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.identity.models.doris import DorisQueryIdentity
from app.query.services.principal import (
    QueryPrincipalNotConfiguredError,
    QueryPrincipalService,
)
from tests.identity.test_auth_service import build_user


def identity(role: str, user: str) -> DorisQueryIdentity:
    """构造持久化查询身份。"""
    return DorisQueryIdentity(
        role_name=role,
        description=role,
        query_user=user,
        encrypted_password="encrypted",
        workload_group="normal",
        is_default=False,
    )


class QueryPrincipalServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证用户只能选择其唯一角色的共享查询账号。"""

    async def test_exact_role_selects_and_decrypts_query_identity(self) -> None:
        user = build_user(doris_role="sales")
        user_provider = MagicMock()
        user_provider.get_user_by_id = AsyncMock(return_value=user)
        identity_provider = MagicMock()
        identity_provider.get = AsyncMock(return_value=identity("sales", "sales_query"))
        cipher = MagicMock()
        cipher.decrypt.return_value = "query_password"
        service = QueryPrincipalService(user_provider, identity_provider, cipher)

        resolved = await service.resolve(user.id)

        self.assertEqual(resolved.role_name, "sales")
        self.assertEqual(
            resolved.authorization_epoch,
            identity_provider.get.return_value.authorization_epoch,
        )
        self.assertEqual(resolved.query_user, "sales_query")
        self.assertEqual(resolved.password, "query_password")
        self.assertNotIn("query_password", repr(resolved))
        cipher.decrypt.assert_called_once_with("encrypted")

    async def test_missing_identity_fails_closed(self) -> None:
        user = build_user(doris_role="unknown")
        user_provider = MagicMock()
        user_provider.get_user_by_id = AsyncMock(return_value=user)
        identity_provider = MagicMock()
        identity_provider.get = AsyncMock(return_value=None)

        with self.assertRaises(QueryPrincipalNotConfiguredError):
            await QueryPrincipalService(
                user_provider,
                identity_provider,
                MagicMock(),
            ).resolve(user.id)


if __name__ == "__main__":
    unittest.main()
