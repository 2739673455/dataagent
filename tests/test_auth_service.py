"""用户认证与令牌生命周期服务测试"""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.conf.app_config import AuthConfig
from app.entities.auth import PlatformRole, RefreshToken, Role, User
from app.errors import auth_error
from app.repositories.auth_pg_repo import AuthPGRepo
from app.services.auth_service import (
    Argon2PasswordManager,
    AuthService,
    JWTCodec,
)


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


class LockedTransactionStub:
    """测试用串行化事务上下文"""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self._lock.release()


def build_concurrent_repo() -> tuple[AuthPGRepo, dict[int, User]]:
    """构造共享状态的并发用户存储替身"""
    repo = MagicMock(spec=AuthPGRepo)
    lock = asyncio.Lock()
    users: dict[int, User] = {}
    repo.transaction.side_effect = lambda: LockedTransactionStub(lock)
    repo.lock_user_provisioning = AsyncMock()
    repo.ensure_base_roles = AsyncMock()

    async def get_by_username(username: str) -> User | None:
        return next((user for user in users.values() if user.username == username), None)

    async def get_by_email(email: str) -> User | None:
        return next((user for user in users.values() if user.email == email), None)

    async def add_user(user: User) -> User:
        user.id = len(users) + 1
        users[user.id] = user
        return user

    async def set_user_roles(
        user_id: int,
        roles: set[PlatformRole] | frozenset[PlatformRole],
    ) -> None:
        users[user_id].roles = [
            Role(name=role.value, description=role.value) for role in roles
        ]

    repo.get_user_by_username.side_effect = get_by_username
    repo.get_user_by_email.side_effect = get_by_email
    repo.add_user.side_effect = add_user
    repo.set_user_roles.side_effect = set_user_roles
    repo.get_user_by_id.side_effect = lambda user_id: users.get(user_id)
    repo.add_refresh_token = AsyncMock()
    return cast(AuthPGRepo, repo), users


def build_config() -> AuthConfig:
    """构造测试认证配置"""
    return AuthConfig(
        jwt_secret="a-secure-test-key-with-at-least-32-characters",
        jwt_algorithm="HS256",
        issuer="dataagent-test",
        access_token_minutes=15,
        refresh_token_days=30,
        password_min_length=10,
    )


def build_user(
    user_id: int = 7,
    role: PlatformRole = PlatformRole.ANALYST,
) -> User:
    """构造已加载角色的用户实体"""
    return User(
        id=user_id,
        username="analyst",
        email="analyst@example.com",
        password_hash="hashed-password",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        roles=[Role(name=role.value, description=role.value)],
    )


def build_repo() -> MagicMock:
    """构造认证存储测试替身"""
    repo = MagicMock(spec=AuthPGRepo)
    repo.transaction.return_value = AsyncTransactionStub()
    repo.lock_user_provisioning = AsyncMock()
    repo.ensure_base_roles = AsyncMock()
    repo.get_user_by_username = AsyncMock()
    repo.get_user_by_email = AsyncMock()
    repo.add_user = AsyncMock()
    repo.set_user_roles = AsyncMock()
    repo.get_user_by_id = AsyncMock()
    repo.add_refresh_token = AsyncMock()
    repo.get_refresh_token_for_update = AsyncMock()
    repo.revoke_refresh_family = AsyncMock()
    repo.rotate_refresh_token.side_effect = AuthPGRepo.rotate_refresh_token
    return repo


class JWTCodecTest(unittest.TestCase):
    """验证 JWT 签名、有效期与令牌类型隔离"""

    def setUp(self) -> None:
        self.config = build_config()
        self.codec = JWTCodec(self.config)
        self.user = build_user()

    def test_access_token_round_trip(self) -> None:
        now = datetime.now(UTC)
        token, token_id = self.codec.issue_access_token(self.user, now)

        claims = self.codec.decode_access_token(token)

        self.assertEqual(claims.user_id, self.user.id)
        self.assertEqual(claims.token_id, token_id)
        self.assertGreater(claims.expires_at, claims.issued_at)

    def test_refresh_token_cannot_be_used_as_access_token(self) -> None:
        now = datetime.now(UTC)
        token_id = JWTCodecTest._uuid(1)
        family_id = JWTCodecTest._uuid(2)
        token = self.codec.issue_refresh_token(
            self.user.id,
            token_id,
            family_id,
            now,
        )

        with self.assertRaises(auth_error.InvalidTokenError):
            self.codec.decode_access_token(token)

    def test_expired_access_token_is_rejected(self) -> None:
        token, _ = self.codec.issue_access_token(
            self.user,
            datetime.now(UTC) - timedelta(hours=1),
        )

        with self.assertRaises(auth_error.InvalidTokenError):
            self.codec.decode_access_token(token)

    @staticmethod
    def _uuid(value: int):
        from uuid import UUID

        return UUID(int=value)


class Argon2PasswordManagerTest(unittest.IsolatedAsyncioTestCase):
    """验证密码使用 Argon2id 哈希"""

    async def test_hash_and_verify(self) -> None:
        async def run_inline(function, *args):
            return function(*args)

        manager = Argon2PasswordManager()
        with patch(
            "app.services.auth_service.to_thread.run_sync",
            new=run_inline,
        ):
            password_hash = await manager.hash("correct horse battery staple")
            verified = await manager.verify(
                "correct horse battery staple",
                password_hash,
            )
            rejected = await manager.verify("wrong password", password_hash)

        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(verified)
        self.assertFalse(rejected)


class AuthServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证注册默认角色与刷新令牌轮换"""

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

    async def test_fresh_database_public_registration_receives_viewer_role(self) -> None:
        loaded = build_user(role=PlatformRole.VIEWER)
        self.repo.get_user_by_username.return_value = None
        self.repo.get_user_by_email.return_value = None

        async def add_user(user: User) -> User:
            user.id = loaded.id
            return user

        self.repo.add_user.side_effect = add_user
        self.repo.get_user_by_id.return_value = loaded

        user, token_pair = await self.service.register(
            "First.Admin",
            "Admin@Example.com",
            "long-enough-password",
        )

        self.assertEqual(user.role_names, {PlatformRole.VIEWER})
        self.repo.lock_user_provisioning.assert_awaited_once()
        self.repo.set_user_roles.assert_awaited_once_with(
            loaded.id,
            {PlatformRole.VIEWER},
        )
        self.assertTrue(token_pair.access_token)
        self.assertTrue(token_pair.refresh_token)
        stored = self.repo.add_refresh_token.await_args.args[0]
        self.assertNotEqual(stored.token_hash, token_pair.refresh_token)

    async def test_later_registered_user_receives_viewer_role(self) -> None:
        loaded = build_user(role=PlatformRole.VIEWER)
        self.repo.get_user_by_username.return_value = None
        self.repo.get_user_by_email.return_value = None

        async def add_user(user: User) -> User:
            user.id = loaded.id
            return user

        self.repo.add_user.side_effect = add_user
        self.repo.get_user_by_id.return_value = loaded

        await self.service.register(
            "New.Analyst",
            "New@Example.com",
            "long-enough-password",
        )

        self.repo.set_user_roles.assert_awaited_once_with(
            loaded.id,
            {PlatformRole.VIEWER},
        )

    async def test_bootstrap_creates_explicit_admin_without_tokens(self) -> None:
        loaded = build_user(role=PlatformRole.ADMIN)
        self.repo.get_user_by_username.return_value = None
        self.repo.get_user_by_email.return_value = None

        async def add_user(user: User) -> User:
            user.id = loaded.id
            return user

        self.repo.add_user.side_effect = add_user
        self.repo.get_user_by_id.return_value = loaded

        result = await self.service.bootstrap_admin(
            "Platform.Admin",
            "Platform.Admin@example.com",
            "long-enough-password",
        )

        self.assertTrue(result.created)
        self.assertTrue(result.admin_granted)
        self.assertEqual(result.user.role_names, {PlatformRole.ADMIN})
        self.repo.set_user_roles.assert_awaited_once_with(
            loaded.id,
            {PlatformRole.ADMIN},
        )
        self.repo.add_refresh_token.assert_not_awaited()

    async def test_bootstrap_refuses_preclaimed_identity_with_other_password(
        self,
    ) -> None:
        existing = build_user(role=PlatformRole.VIEWER)
        self.repo.get_user_by_username.return_value = existing
        self.repo.get_user_by_email.return_value = existing
        self.password_manager.verify.return_value = False

        with self.assertRaises(auth_error.UserAlreadyExistsError):
            await self.service.bootstrap_admin(
                existing.username,
                existing.email,
                "operator-bootstrap-password",
            )

        self.repo.set_user_roles.assert_not_awaited()

    async def test_concurrent_public_registration_cannot_claim_admin(self) -> None:
        repo, users = build_concurrent_repo()
        service = AuthService(
            repo,
            build_config(),
            self.password_manager,
            now=lambda: self.now,
        )

        await asyncio.gather(
            *(
                service.register(
                    f"user-{index}",
                    f"user-{index}@example.com",
                    "long-enough-password",
                )
                for index in range(20)
            )
        )

        self.assertEqual(len(users), 20)
        self.assertTrue(
            all(user.role_names == {PlatformRole.VIEWER} for user in users.values())
        )

    async def test_concurrent_admin_bootstrap_is_idempotent(self) -> None:
        repo, users = build_concurrent_repo()
        service = AuthService(
            repo,
            build_config(),
            self.password_manager,
            now=lambda: self.now,
        )

        results = await asyncio.gather(
            *(
                service.bootstrap_admin(
                    "bootstrap-admin",
                    "bootstrap@example.com",
                    "long-enough-password",
                )
                for _ in range(8)
            )
        )

        self.assertEqual(len(users), 1)
        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual(sum(result.admin_granted for result in results), 1)
        self.assertEqual(
            next(iter(users.values())).role_names,
            {PlatformRole.ADMIN},
        )

    async def test_refresh_rotates_token_and_replay_revokes_family(self) -> None:
        user = build_user()
        self.repo.get_user_by_username.return_value = user

        _, initial_pair = await self.service.login(user.username, "password")
        current = self.repo.add_refresh_token.await_args.args[0]
        self.assertIsInstance(current, RefreshToken)

        self.repo.get_refresh_token_for_update.return_value = current
        self.repo.get_user_by_id.return_value = user
        self.repo.add_refresh_token.reset_mock()

        _, rotated_pair = await self.service.refresh(initial_pair.refresh_token)

        replacement = self.repo.add_refresh_token.await_args.args[0]
        self.assertEqual(replacement.family_id, current.family_id)
        self.assertEqual(current.replaced_by_id, replacement.id)
        self.assertEqual(current.revoked_at, self.now)
        self.assertNotEqual(rotated_pair.refresh_token, initial_pair.refresh_token)

        with self.assertRaises(auth_error.RefreshTokenReuseError):
            await self.service.refresh(initial_pair.refresh_token)

        self.repo.revoke_refresh_family.assert_awaited_with(
            current.family_id,
            self.now,
        )

    async def test_wrong_password_does_not_issue_tokens(self) -> None:
        self.repo.get_user_by_email.return_value = build_user()
        self.password_manager.verify.return_value = False

        with self.assertRaises(auth_error.InvalidCredentialsError):
            await self.service.login("analyst@example.com", "wrong")

        self.repo.add_refresh_token.assert_not_awaited()

    async def test_argon2_work_is_bounded_by_shared_semaphore(self) -> None:
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
        with patch(
            "app.services.auth_service.to_thread.run_sync",
            new=run_slowly,
        ):
            await asyncio.gather(
                *(manager.hash(f"password-{index}") for index in range(8))
            )

        self.assertEqual(max_active, 2)
