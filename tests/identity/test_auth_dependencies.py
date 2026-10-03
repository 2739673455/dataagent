"""认证依赖会话边界测试。"""

import unittest
from contextlib import asynccontextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import _get_current_user
from app.identity import IdentityService
from app.identity.auth.tokens import JWTCodec
from app.identity.contracts import AuthenticatedUser
from app.identity.errors import AuthenticationRequiredError, PermissionDeniedError
from app.shared.config.app_config import cfg
from tests.identity.test_auth_service import build_user


class AuthDependencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_current_user_uses_independent_short_lived_session(self) -> None:
        session = AsyncSession()

        @asynccontextmanager
        async def session_scope():
            async with session:
                yield session

        user = build_user()
        principal = AuthenticatedUser(
            **{
                field.name: getattr(user, field.name)
                for field in fields(AuthenticatedUser)
            }
        )
        repo = MagicMock()
        repo.get_user_by_id = AsyncMock(return_value=user)
        token = JWTCodec(cfg.auth).issue_access_token(user, datetime.now(UTC))
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )

        resources = MagicMock()
        resources.identity = IdentityService(resources.auth, resources.admin_doris)
        with (
            patch.object(
                resources.auth,
                "session",
                return_value=session_scope(),
            ) as create_session,
            patch(
                "app.identity.service.IdentityPGRepo",
                return_value=repo,
            ) as create_repo,
        ):
            result = await _get_current_user(resources, credentials)

        self.assertEqual(result, principal)
        create_session.assert_called_once_with()
        create_repo.assert_called_once_with(session)
        repo.get_user_by_id.assert_awaited_once_with(user.id)
        self.assertFalse(session.in_transaction())

    async def test_missing_bearer_credentials_do_not_call_identity(self) -> None:
        for credentials in (
            None,
            HTTPAuthorizationCredentials(scheme="Basic", credentials="token"),
        ):
            with self.subTest(credentials=credentials):
                resources = MagicMock()
                resources.identity.authenticate = AsyncMock()
                with self.assertRaises(AuthenticationRequiredError):
                    await _get_current_user(resources, credentials)
                resources.identity.authenticate.assert_not_awaited()

    async def test_analysis_qualification_requires_managed_role_and_closes_session(
        self,
    ) -> None:
        user = build_user()
        principal = AuthenticatedUser(
            **{
                field.name: getattr(user, field.name)
                for field in fields(AuthenticatedUser)
            }
        )
        for role, managed in (
            (None, False),
            (user.doris_role_name, False),
            (user.doris_role_name, True),
        ):
            with self.subTest(role=role, managed=managed):
                active = []

                @asynccontextmanager
                async def session_scope(active=active):
                    active.append(True)
                    try:
                        yield MagicMock()
                    finally:
                        active.clear()

                async def get_identity(
                    role_name, active=active, role=role, managed=managed
                ):
                    self.assertTrue(active)
                    self.assertEqual(role_name, role)
                    return MagicMock() if managed else None

                resources = MagicMock()
                resources.auth.session.side_effect = session_scope
                service = IdentityService(resources.auth, resources.admin_doris)
                repo = MagicMock()
                repo.get_query_identity = AsyncMock(side_effect=get_identity)
                with patch(
                    "app.identity.service.IdentityPGRepo",
                    return_value=repo,
                ):
                    if managed:
                        await service.require_analysis_access(
                            replace(principal, doris_role_name=role)
                        )
                    else:
                        with self.assertRaises(PermissionDeniedError):
                            await service.require_analysis_access(
                                replace(principal, doris_role_name=role)
                            )
                self.assertFalse(active)
                if role is None:
                    resources.auth.session.assert_not_called()

        with self.assertRaises(PermissionDeniedError):
            IdentityService.require_admin(principal)
        IdentityService.require_admin(replace(principal, is_admin=True))
