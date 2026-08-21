"""用户认证与令牌生命周期服务测试"""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.conf.app_config import AuthConfig
from app.errors import auth_error
from app.models.auth import RefreshToken, User
from app.repositories.auth_pg_repo import AuthPGRepo
from app.services.auth_service import Argon2PasswordManager, AuthService, JWTCodec

DEFAULT_ROLE = "dataagent_default"


class AsyncTransactionStub:
    """测试用异步事务上下文"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


def build_config() -> AuthConfig:
    """构造测试认证配置"""
    return AuthConfig(
        jwt_secret="a-secure-test-key-with-at-least-32-characters",
        jwt_algorithm="HS256",
        issuer="dataagent-test",
        access_token_minutes=15,
        refresh_token_days=30,
        password_min_length=6,
    )


def build_user(
    user_id: int = 7,
    *,
    is_admin: bool = False,
    doris_role: str | None = DEFAULT_ROLE,
) -> User:
    """构造用户实体"""
    return User(
        id=user_id,
        username="analyst",
        email="analyst@example.com",
        password_hash="hashed-password",
        auth_version=0,
        is_active=True,
        is_admin=is_admin,
        doris_role_name=doris_role,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def build_repo() -> MagicMock:
    """构造认证存储测试替身"""
    repo = MagicMock(spec=AuthPGRepo)
    repo.transaction.return_value = AsyncTransactionStub()
    repo.lock_security_mutation = AsyncMock()
    repo.get_user_by_username = AsyncMock()
    repo.get_user_by_email = AsyncMock()
    repo.get_user_by_username_for_update = AsyncMock()
    repo.get_user_by_email_for_update = AsyncMock()
    repo.add_user = AsyncMock()
    repo.set_user_admin = AsyncMock()
    repo.get_user_by_id = AsyncMock()
    repo.get_user_by_id_for_update = AsyncMock()
    repo.set_user_password = AsyncMock()
    repo.add_refresh_token = AsyncMock()
    repo.get_refresh_token_for_update = AsyncMock()
    repo.revoke_refresh_family = AsyncMock()
    repo.revoke_user_refresh_tokens = AsyncMock()
    repo.rotate_refresh_token.side_effect = AuthPGRepo.rotate_refresh_token
    return repo


class JWTCodecTest(unittest.TestCase):
    """验证 JWT 签名、有效期与令牌类型隔离"""

    def setUp(self) -> None:
        self.codec = JWTCodec(build_config())
        self.user = build_user()

    def test_access_token_round_trip(self) -> None:
        now = datetime.now(UTC)
        token, token_id = self.codec.issue_access_token(self.user, now)

        claims = self.codec.decode_access_token(token)

        self.assertEqual(claims.user_id, self.user.id)
        self.assertEqual(claims.token_id, token_id)

    def test_expired_access_token_is_rejected(self) -> None:
        token, _ = self.codec.issue_access_token(
            self.user,
            datetime.now(UTC) - timedelta(hours=1),
        )

        with self.assertRaises(auth_error.InvalidTokenError):
            self.codec.decode_access_token(token)


class Argon2PasswordManagerTest(unittest.IsolatedAsyncioTestCase):
    """验证密码使用 Argon2id 哈希"""

    async def test_hash_and_verify(self) -> None:
        async def run_inline(function, *args):
            return function(*args)

        manager = Argon2PasswordManager()
        with patch("app.services.auth_service.to_thread.run_sync", new=run_inline):
            password_hash = await manager.hash("correct horse battery staple")
            verified = await manager.verify(
                "correct horse battery staple",
                password_hash,
            )

        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(verified)

    async def test_work_is_bounded_by_shared_semaphore(self) -> None:
        active = 0
        max_active = 0

        async def run_slowly(function, *args):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "hash"

        manager = Argon2PasswordManager(max_concurrency=2)
        with patch("app.services.auth_service.to_thread.run_sync", new=run_slowly):
            await asyncio.gather(*(manager.hash(str(index)) for index in range(8)))

        self.assertEqual(max_active, 2)


class AuthServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证管理员引导与刷新令牌轮换"""

    async def asyncSetUp(self) -> None:
        self.repo = build_repo()
        self.password_manager = MagicMock()
        self.password_manager.hash = AsyncMock(return_value="hashed-password")
        self.password_manager.verify = AsyncMock(return_value=True)
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.service = AuthService(
            self.repo,
            build_config(),
            self.password_manager,
            now=lambda: self.now,
        )

    async def test_bootstrap_creates_admin_without_privileged_doris_role(self) -> None:
        self.repo.get_user_by_username.return_value = None
        self.repo.get_user_by_email.return_value = None

        async def add_user(user: User) -> User:
            user.id = 7
            return user

        self.repo.add_user.side_effect = add_user
        self.repo.get_user_by_id.side_effect = lambda user_id: self.repo.add_user.await_args.args[0]

        result = await self.service.bootstrap_admin(
            "Platform.Admin",
            "Platform.Admin@example.com",
            "long-enough-password",
        )

        self.assertTrue(result.created)
        self.assertTrue(result.user.is_admin)
        self.assertIsNone(result.user.doris_role_name)
        self.repo.add_refresh_token.assert_not_awaited()

    async def test_bootstrap_promotes_matching_existing_user(self) -> None:
        existing = build_user()
        self.repo.get_user_by_username.return_value = existing
        self.repo.get_user_by_email.return_value = existing
        self.repo.get_user_by_id.return_value = existing

        result = await self.service.bootstrap_admin(
            existing.username,
            existing.email,
            "long-enough-password",
        )

        self.assertTrue(result.admin_granted)
        self.repo.set_user_admin.assert_awaited_once_with(existing, True)

    async def test_refresh_rotates_token_and_replay_revokes_family(self) -> None:
        user = build_user()
        self.repo.get_user_by_username_for_update.return_value = user
        _, initial_pair = await self.service.login(user.username, "password")
        current = self.repo.add_refresh_token.await_args.args[0]
        self.assertIsInstance(current, RefreshToken)
        self.repo.get_refresh_token_for_update.return_value = current
        self.repo.get_user_by_id_for_update.return_value = user
        self.repo.add_refresh_token.reset_mock()

        await self.service.refresh(initial_pair.refresh_token)

        replacement = self.repo.add_refresh_token.await_args.args[0]
        self.assertEqual(replacement.family_id, current.family_id)
        with self.assertRaises(auth_error.RefreshTokenReuseError):
            await self.service.refresh(initial_pair.refresh_token)
        self.repo.revoke_refresh_family.assert_awaited_with(current.family_id, self.now)

    async def test_wrong_password_does_not_issue_tokens(self) -> None:
        self.repo.get_user_by_email_for_update.return_value = build_user()
        self.password_manager.verify.return_value = False

        with self.assertRaises(auth_error.InvalidCredentialsError):
            await self.service.login("analyst@example.com", "wrong")

        self.repo.add_refresh_token.assert_not_awaited()

    async def test_change_password_updates_hash_and_revokes_refresh_tokens(self) -> None:
        user = build_user()
        self.repo.get_user_by_id_for_update.return_value = user
        self.password_manager.verify.return_value = True
        self.password_manager.hash.return_value = "new-hash"

        await self.service.change_password(
            user.id,
            "current-password",
            "new-password",
        )

        self.repo.set_user_password.assert_awaited_once_with(user, "new-hash")
        self.repo.revoke_user_refresh_tokens.assert_awaited_once_with(
            user.id,
            self.now,
        )

    async def test_change_password_rejects_wrong_current_password(self) -> None:
        user = build_user()
        self.repo.get_user_by_id_for_update.return_value = user
        self.password_manager.verify.return_value = False

        with self.assertRaises(auth_error.InvalidCurrentPasswordError):
            await self.service.change_password(
                user.id,
                "wrong-password",
                "new-password",
            )

        self.repo.set_user_password.assert_not_awaited()
        self.repo.revoke_user_refresh_tokens.assert_not_awaited()

    async def test_auth_version_change_invalidates_existing_access_token(self) -> None:
        user = build_user()
        self.repo.get_user_by_username_for_update.return_value = user
        _, token_pair = await self.service.login(user.username, "password")
        user.auth_version += 1
        self.repo.get_user_by_id.return_value = user

        with self.assertRaises(auth_error.InvalidTokenError):
            await self.service.authenticate_access_token(token_pair.access_token)
