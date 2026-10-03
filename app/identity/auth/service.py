"""管理员引导、登录和令牌生命周期用例。"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.accounts.validation import (
    ensure_active_user,
    validate_email,
    validate_password,
    validate_username,
)
from app.identity.auth.passwords import Argon2PasswordManager, get_password_manager
from app.identity.auth.tokens import JWTCodec, TokenPair
from app.identity.models.account import RefreshToken, User
from app.identity.repositories.identity import IdentityPGRepo
from app.shared.config.app_config import AuthConfig


@dataclass(frozen=True)
class BootstrapAdminResult:
    """管理员引导创建结果。"""

    user: User
    created: bool
    admin_granted: bool


class AuthService:
    """管理员引导、登录与令牌生命周期服务。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        config: AuthConfig,
        password_manager: Argon2PasswordManager | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化认证仓储、密码哈希器和令牌编解码器。"""
        self._repo = repo
        self._config = config
        self._password_manager = (
            password_manager if password_manager is not None else get_password_manager()
        )
        self._codec = JWTCodec(config)
        self._now = now or (lambda: datetime.now(UTC))

    async def bootstrap_admin(
        self,
        username: str,
        email: str,
        password: str,
    ) -> BootstrapAdminResult:
        """使用显式凭据幂等创建或确认管理员。"""
        normalized_username = validate_username(username)
        normalized_email = validate_email(email)
        validate_password(password, min_length=self._config.password_min_length)
        password_hash = await self._password_manager.hash(password)

        try:
            async with self._repo.session.begin():
                # 多个初始化进程可能同时启动；安全变更锁保证最多创建或提升同一账号一次。
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
                            detail="初始化账号与现有账号冲突"
                        )
                    ensure_active_user(existing)
                    admin_granted = not existing.is_admin
                    if admin_granted:
                        await self._repo.update_user(
                            existing,
                            doris_role=None,
                            update_doris_role=False,
                            is_admin=True,
                        )
                    loaded = await self._repo.get_user_by_id(existing.id)
                    if loaded is None:
                        raise RuntimeError("初始化管理员账号加载失败")
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
                    raise RuntimeError("初始化管理员账号加载失败")
                return BootstrapAdminResult(
                    user=loaded,
                    created=True,
                    admin_granted=True,
                )
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError(
                detail="初始化账号与现有账号冲突"
            ) from exc

    async def login(self, identifier: str, password: str) -> tuple[User, TokenPair]:
        """校验账号密码并签发令牌对。"""
        normalized = identifier.strip().casefold()
        async with self._repo.session.begin():
            user = (
                await self._repo.get_user_by_email_for_update(normalized)
                if "@" in normalized
                else await self._repo.get_user_by_username_for_update(normalized)
            )
            if user is None:
                await self._password_manager.verify_dummy_password(password)
                raise auth_error.InvalidCredentialsError
            if not await self._password_manager.verify(password, user.password_hash):
                raise auth_error.InvalidCredentialsError
            ensure_active_user(user)
            token_pair = await self._issue_token_pair(user, uuid4())
        logger.info(f"用户登录成功: user_id={user.id}, username={user.username}")
        return user, token_pair

    async def refresh(self, refresh_token: str) -> tuple[User, TokenPair]:
        """轮换刷新令牌并签发新令牌对。"""
        claims = self._codec.decode_refresh_token(refresh_token)
        token_digest = self.digest_token(refresh_token)
        now = self._now()
        reuse_detected = False
        loaded_user: User | None = None
        token_pair: TokenPair | None = None

        async with self._repo.session.begin():
            loaded_user = await self._repo.get_user_by_id_for_update(claims.user_id)
            current = await self._repo.get_refresh_token_for_update(claims.token_id)
            if (
                loaded_user is None
                or current is None
                or current.user_id != claims.user_id
                or current.family_id != claims.family_id
                or not hmac.compare_digest(current.token_hash, token_digest)
            ):
                raise auth_error.InvalidTokenError
            if current.revoked_at is not None:
                await self._repo.revoke_refresh_family(current.family_id, now)
                reuse_detected = True
            else:
                ensure_active_user(loaded_user)
                replacement_id = uuid4()
                token_pair = await self._issue_token_pair(
                    loaded_user,
                    current.family_id,
                    refresh_token_id=replacement_id,
                )
                self._repo.rotate_refresh_token(current, replacement_id, now)

        if reuse_detected:
            raise auth_error.RefreshTokenReuseError(detail="该刷新令牌已被注销")
        if loaded_user is None or token_pair is None:
            raise RuntimeError("刷新令牌轮换未生成有效令牌对")
        logger.info(f"刷新令牌轮换成功: user_id={loaded_user.id}")
        return loaded_user, token_pair

    async def logout(self, refresh_token: str) -> None:
        """吊销刷新令牌所属的完整令牌族。"""
        claims = self._codec.decode_refresh_token(refresh_token)
        token_digest = self.digest_token(refresh_token)
        async with self._repo.session.begin():
            current = await self._repo.get_refresh_token_for_update(claims.token_id)
            if (
                current is None
                or current.user_id != claims.user_id
                or current.family_id != claims.family_id
                or not hmac.compare_digest(current.token_hash, token_digest)
            ):
                raise auth_error.InvalidTokenError
            await self._repo.revoke_refresh_family(current.family_id, self._now())
        logger.info(f"用户退出登录并吊销令牌族: user_id={claims.user_id}")

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> None:
        """验证当前密码、更新哈希并吊销全部既有令牌。"""
        validate_password(new_password, min_length=self._config.password_min_length)
        if hmac.compare_digest(current_password, new_password):
            raise auth_error.InvalidUserMutationError(detail="新密码不能与当前密码相同")
        password_hash = await self._password_manager.hash(new_password)

        async with self._repo.session.begin():
            user = await self._repo.get_user_by_id_for_update(user_id)
            if user is None:
                raise auth_error.InvalidTokenError
            ensure_active_user(user)
            if not await self._password_manager.verify(
                current_password,
                user.password_hash,
            ):
                raise auth_error.InvalidCurrentPasswordError
            await self._repo.set_user_password(user, password_hash)
            await self._repo.revoke_user_refresh_tokens(user.id, self._now())
        logger.info(f"用户密码修改成功并吊销既有令牌: user_id={user_id}")

    @staticmethod
    def digest_token(token: str) -> str:
        """计算令牌的不可逆存储摘要。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def _issue_token_pair(
        self,
        user: User,
        family_id: UUID,
        *,
        refresh_token_id: UUID | None = None,
    ) -> TokenPair:
        """签发并持久化一个令牌对。"""
        now = self._now()
        access_token = self._codec.issue_access_token(user, now)
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
