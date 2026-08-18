"""Doris 单角色查询身份解析测试"""

import unittest

from app.conf.app_config import DorisRoleConfig
from app.services.query_principal_service import (
    QueryPrincipalNotConfiguredError,
    QueryPrincipalService,
)
from tests.test_auth_service import build_user


def profile(user: str) -> DorisRoleConfig:
    """构造角色查询身份"""
    return DorisRoleConfig(
        description=user,
        is_default=False,
        query_user=user,
        query_password="secret",
        workload_group="normal",
    )


class FakeUserProvider:
    """用户读取替身"""

    def __init__(self, user):
        self.user = user

    async def get_user_by_id(self, user_id: int):
        return self.user if self.user.id == user_id else None


class QueryPrincipalServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证用户只能选择其唯一角色的共享查询账号"""

    async def test_exact_role_selects_exact_query_identity(self) -> None:
        user = build_user(doris_role="sales")
        service = QueryPrincipalService(
            FakeUserProvider(user),
            {"sales": profile("sales_query"), "finance": profile("finance_query")},
        )

        resolved = await service.resolve(user.id)

        self.assertEqual(resolved.role_name, "sales")
        self.assertEqual(resolved.config.query_user, "sales_query")

    async def test_unconfigured_role_fails_closed(self) -> None:
        user = build_user(doris_role="unknown")
        service = QueryPrincipalService(
            FakeUserProvider(user),
            {"sales": profile("sales_query")},
        )

        with self.assertRaises(QueryPrincipalNotConfiguredError):
            await service.resolve(user.id)
