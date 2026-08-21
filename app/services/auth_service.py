"""用户认证与令牌生命周期服务"""

import asyncio
import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import jwt
from anyio import to_thread
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError

from app.conf.app_config import AuthConfig
from app.errors import auth_error
from app.models.auth import RefreshToken, User
from app.repositories.auth_pg_repo import AuthPGRepo

ARGON2_MAX_CONCURRENCY = 4
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,64}$")
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MAX_EMAIL_LENGTH = 320
_MAX_PASSWORD_LENGTH = 128


class PasswordManager(Protocol):
    """异步密码哈希接口"""

    async def hash(self, password: str) -> str: ...

    async def verify(self, password: str, password_hash: str) -> bool: ...


class Argon2PasswordManager:
    """基于 Argon2id 的异步密码哈希实现"""

    def __init__(self, *, max_concurrency: int = ARGON2_MAX_CONCURRENCY) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash("dataagent-dummy-password")
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def hash(self, password: str) -> str:
        """在线程池计算密码哈希"""
        async with self._semaphore:
            return await to_thread.run_sync(self._password_hash.hash, password)

    async def verify(self, password: str, password_hash: str) -> bool:
        """在线程池校验密码"""
        async with self._semaphore:
            return await to_thread.run_sync(
                self._password_hash.verify,
                password,
                password_hash,
            )

    async def consume_dummy_verification(self, password: str) -> None:
        """为不存在的用户执行等价密码计算"""
        await self.verify(password, self._dummy_hash)


@dataclass(frozen=True)
class AccessTokenClaims:
    """已验证的访问令牌载荷"""

    user_id: int
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class RefreshTokenClaims:
    """已验证的刷新令牌载荷"""

    user_id: int
    token_id: UUID
    family_id: UUID
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class TokenPair:
    """访问令牌与刷新令牌"""

    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int


@dataclass(frozen=True)
class BootstrapAdminResult:
    """管理员引导创建结果"""

    user: User
    created: bool
    admin_granted: bool


class JWTCodec:
    """应用 JWT 编解码器"""

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    def issue_access_token(self, user: User, now: datetime) -> tuple[str, UUID]:
        """签发短期访问令牌"""
        token_id = uuid4()
        expires_at = now + timedelta(minutes=self._config.access_token_minutes)
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "jti": str(token_id),
            "token_type": "access",
            "is_admin": user.is_admin,
            "doris_role": user.doris_role_name,
            "iat": now,
            "exp": expires_at,
            "iss": self._config.issuer,
        }
        return (
            jwt.encode(
                payload,
                self._config.jwt_secret,
                algorithm=self._config.jwt_algorithm,
            ),
            token_id,
        )

    def issue_refresh_token(
        self,
        user_id: int,
        token_id: UUID,
        family_id: UUID,
        now: datetime,
    ) -> str:
        """签发长期刷新令牌"""
        return jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(token_id),
                "family_id": str(family_id),
                "token_type": "refresh",
                "iat": now,
                "exp": now + timedelta(days=self._config.refresh_token_days),
                "iss": self._config.issuer,
            },
            self._config.jwt_secret,
            algorithm=self._config.jwt_algorithm,
        )

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        """校验并解析访问令牌"""
        payload = self._decode(token, "access")
        return AccessTokenClaims(
            user_id=self._parse_user_id(payload),
            token_id=self._parse_uuid(payload, "jti"),
            issued_at=self._parse_timestamp(payload, "iat"),
            expires_at=self._parse_timestamp(payload, "exp"),
        )

    def decode_refresh_token(self, token: str) -> RefreshTokenClaims:
        """校验并解析刷新令牌"""
        payload = self._decode(token, "refresh")
        return RefreshTokenClaims(
            user_id=self._parse_user_id(payload),
            token_id=self._parse_uuid(payload, "jti"),
            family_id=self._parse_uuid(payload, "family_id"),
            issued_at=self._parse_timestamp(payload, "iat"),
            expires_at=self._parse_timestamp(payload, "exp"),
        )

    def _decode(self, token: str, expected_type: str) -> dict[str, Any]:
        """验证 JWT 签名、标准声明与令牌类型"""
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.jwt_algorithm],
                issuer=self._config.issuer,
                leeway=5,
                options={"require": ["sub", "jti", "token_type", "iat", "exp", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise auth_error.InvalidTokenError from exc
        if payload.get("token_type") != expected_type:
            raise auth_error.InvalidTokenError(detail="Unexpected token type")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _parse_user_id(payload: dict[str, Any]) -> int:
        """解析用户主键声明"""
        try:
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise auth_error.InvalidTokenError(detail="Invalid subject claim") from exc
        if user_id <= 0:
            raise auth_error.InvalidTokenError(detail="Invalid subject claim")
        return user_id

    @staticmethod
    def _parse_uuid(payload: dict[str, Any], key: str) -> UUID:
        """解析 UUID 声明"""
        try:
            return UUID(str(payload[key]))
        except (KeyError, TypeError, ValueError) as exc:
            raise auth_error.InvalidTokenError(detail=f"Invalid {key} claim") from exc

    @staticmethod
    def _parse_timestamp(payload: dict[str, Any], key: str) -> datetime:
        """解析时间戳声明"""
        value = payload.get(key)
        if not isinstance(value, (int, float)):
            raise auth_error.InvalidTokenError(detail=f"Invalid {key} claim")
        return datetime.fromtimestamp(value, UTC)


class AuthService:
    """管理员引导、登录与令牌生命周期服务"""

    def __init__(
        self,
        repo: AuthPGRepo,
        config: AuthConfig,
        password_manager: PasswordManager,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._config = config
        self._password_manager = password_manager
        self._codec = JWTCodec(config)
        self._now = now or (lambda: datetime.now(UTC))

    async def bootstrap_admin(
        self,
        username: str,
        email: str,
        password: str,
    ) -> BootstrapAdminResult:
        """使用显式凭据幂等创建或确认管理员"""
        normalized_username = self.normalize_username(username)
        normalized_email = self.normalize_email(email)
        self._validate_new_identity(normalized_username, normalized_email)
        self._validate_password(password)
        password_hash = await self._password_manager.hash(password)

        try:
            async with self._repo.transaction():
                await self._repo.lock_security_mutation()
                by_username = await self._repo.get_user_by_username(normalized_username)
                by_email = await self._repo.get_user_by_email(normalized_email)
                existing = by_username or by_email
                if existing is not None:
                    if (
                        by_username is None
                        or by_email is None
                        or by_username.id != by_email.id
                        or not await self._password_manager.verify(
                            password,
                            existing.password_hash,
                        )
                    ):
                        raise auth_error.UserAlreadyExistsError(
                            detail="Bootstrap identity conflicts with an existing account"
                        )
                    self._ensure_active(existing)
                    admin_granted = not existing.is_admin
                    if admin_granted:
                        await self._repo.set_user_admin(existing, True)
                    loaded = await self._repo.get_user_by_id(existing.id)
                    if loaded is None:
                        raise RuntimeError("Bootstrap user could not be reloaded")
                    return BootstrapAdminResult(
                        user=loaded,
                        created=False,
                        admin_granted=admin_granted,
                    )

                user = await self._repo.add_user(
                    User(
                        username=normalized_username,
                        email=normalized_email,
                        password_hash=password_hash,
                        is_active=True,
                        is_admin=True,
                        doris_role_name=None,
                    )
                )
                loaded = await self._repo.get_user_by_id(user.id)
                if loaded is None:
                    raise RuntimeError("Bootstrap user could not be reloaded")
                return BootstrapAdminResult(
                    user=loaded,
                    created=True,
                    admin_granted=True,
                )
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError(
                detail="Bootstrap identity conflicts with an existing account"
            ) from exc

    async def login(self, identifier: str, password: str) -> tuple[User, TokenPair]:
        """校验账号密码并签发令牌对"""
        normalized = identifier.strip().casefold()
        async with self._repo.transaction():
            user = (
                await self._repo.get_user_by_email(normalized)
                if "@" in normalized
                else await self._repo.get_user_by_username(normalized)
            )
            if user is None:
                consume_dummy = getattr(
                    self._password_manager,
                    "consume_dummy_verification",
                    None,
                )
                if consume_dummy is not None:
                    await consume_dummy(password)
                raise auth_error.InvalidCredentialsError
            if not await self._password_manager.verify(password, user.password_hash):
                raise auth_error.InvalidCredentialsError
            self._ensure_active(user)
            token_pair = await self._issue_token_pair(user, uuid4())
        return user, token_pair

    async def refresh(self, refresh_token: str) -> tuple[User, TokenPair]:
        """轮换刷新令牌并签发新令牌对"""
        claims = self._codec.decode_refresh_token(refresh_token)
        token_digest = self.digest_token(refresh_token)
        now = self._now()
        reuse_detected = False
        loaded_user: User | None = None
        token_pair: TokenPair | None = None

        async with self._repo.transaction():
            current = await self._repo.get_refresh_token_for_update(claims.token_id)
            if (
                current is None
                or current.user_id != claims.user_id
                or current.family_id != claims.family_id
                or not hmac.compare_digest(current.token_hash, token_digest)
            ):
                raise auth_error.InvalidTokenError
            if current.revoked_at is not None:
                await self._repo.revoke_refresh_family(current.family_id, now)
                reuse_detected = True
            else:
                loaded_user = await self._repo.get_user_by_id(current.user_id)
                if loaded_user is None:
                    raise auth_error.InvalidTokenError
                self._ensure_active(loaded_user)
                replacement_id = uuid4()
                token_pair = await self._issue_token_pair(
                    loaded_user,
                    current.family_id,
                    refresh_token_id=replacement_id,
                )
                self._repo.rotate_refresh_token(current, replacement_id, now)

        if reuse_detected:
            raise auth_error.RefreshTokenReuseError(
                detail="The refresh token family has been revoked"
            )
        if loaded_user is None or token_pair is None:
            raise RuntimeError("Refresh token rotation did not produce a token pair")
        return loaded_user, token_pair

    async def logout(self, refresh_token: str) -> None:
        """吊销刷新令牌所属的完整令牌族"""
        claims = self._codec.decode_refresh_token(refresh_token)
        token_digest = self.digest_token(refresh_token)
        async with self._repo.transaction():
            current = await self._repo.get_refresh_token_for_update(claims.token_id)
            if (
                current is None
                or current.user_id != claims.user_id
                or current.family_id != claims.family_id
                or not hmac.compare_digest(current.token_hash, token_digest)
            ):
                raise auth_error.InvalidTokenError
            await self._repo.revoke_refresh_family(current.family_id, self._now())

    async def authenticate_access_token(self, access_token: str) -> User:
        """校验访问令牌并加载当前用户权限"""
        claims = self._codec.decode_access_token(access_token)
        user = await self._repo.get_user_by_id(claims.user_id)
        if user is None:
            raise auth_error.InvalidTokenError
        self._ensure_active(user)
        return user

    async def _issue_token_pair(
        self,
        user: User,
        family_id: UUID,
        *,
        refresh_token_id: UUID | None = None,
    ) -> TokenPair:
        """签发并持久化一个令牌对"""
        now = self._now()
        access_token, _ = self._codec.issue_access_token(user, now)
        token_id = refresh_token_id or uuid4()
        refresh_token = self._codec.issue_refresh_token(
            user.id,
            token_id,
            family_id,
            now,
        )
        refresh_expires_at = now + timedelta(days=self._config.refresh_token_days)
        await self._repo.add_refresh_token(
            RefreshToken(
                id=token_id,
                family_id=family_id,
                user_id=user.id,
                token_hash=self.digest_token(refresh_token),
                expires_at=refresh_expires_at,
            )
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_in=self._config.access_token_minutes * 60,
            refresh_expires_in=self._config.refresh_token_days * 24 * 60 * 60,
        )

    @staticmethod
    def digest_token(token: str) -> str:
        """计算令牌的不可逆存储摘要"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_username(username: str) -> str:
        """规范化用户名"""
        return username.strip().casefold()

    @staticmethod
    def normalize_email(email: str) -> str:
        """规范化邮箱"""
        return email.strip().casefold()

    def _validate_password(self, password: str) -> None:
        """校验新密码长度边界"""
        if len(password) < self._config.password_min_length:
            raise ValueError(
                f"password must contain at least {self._config.password_min_length} characters"
            )
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise ValueError(
                f"password must contain at most {_MAX_PASSWORD_LENGTH} characters"
            )

    @staticmethod
    def _validate_new_identity(username: str, email: str) -> None:
        """校验注册与管理员引导使用的规范化身份"""
        if _USERNAME_PATTERN.fullmatch(username) is None:
            raise ValueError("username format is invalid")
        if len(email) > _MAX_EMAIL_LENGTH or _EMAIL_PATTERN.fullmatch(email) is None:
            raise ValueError("email format is invalid")

    @staticmethod
    def _ensure_active(user: User) -> None:
        """确保用户仍可登录"""
        if not user.is_active:
            raise auth_error.InactiveUserError
