"""认证依赖会话边界测试"""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.api.auth.dependencies import get_current_user
from app.identity.services.auth import AuthenticatedUser
from app.shared.config.app_config import cfg
from tests.identity.test_auth_service import build_user


class AuthDependencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_current_user_uses_independent_short_lived_session(self) -> None:
        session = AsyncSession()

        @asynccontextmanager
        async def session_scope():
            async with session:
                yield session

        principal = AuthenticatedUser.from_user(build_user())
        authenticator = MagicMock()
        authenticator.authenticate = AsyncMock(return_value=principal)
        repo = MagicMock()
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="access-token",
        )

        with (
            patch(
                "app.identity.api.auth.dependencies."
                "auth_postgres_client_manager.session",
                return_value=session_scope(),
            ) as create_session,
            patch(
                "app.identity.api.auth.dependencies.AuthPGRepo",
                return_value=repo,
            ) as create_repo,
            patch(
                "app.identity.api.auth.dependencies.AccessTokenAuthenticator",
                return_value=authenticator,
            ) as create_authenticator,
        ):
            result = await get_current_user(credentials)

        self.assertIs(result, principal)
        create_session.assert_called_once_with()
        create_repo.assert_called_once_with(session)
        create_authenticator.assert_called_once_with(repo, cfg.auth)
        authenticator.authenticate.assert_awaited_once_with("access-token")
