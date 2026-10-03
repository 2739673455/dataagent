"""平台用户创建、查询与修改。"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.identity import errors as auth_error
from app.identity.accounts.validation import (
    validate_email,
    validate_password,
    validate_username,
)
from app.identity.auth.passwords import Argon2PasswordManager, get_password_manager
from app.identity.models.account import User
from app.identity.models.doris import normalize_doris_role_name
from app.identity.repositories.identity import IdentityPGRepo
from app.shared.config.app_config import AuthConfig


class UserManagementService:
    """在认证事务中管理账号、角色绑定和管理员权限。"""

    def __init__(
        self,
        repo: IdentityPGRepo,
        config: AuthConfig,
        password_manager: Argon2PasswordManager | None = None,
    ) -> None:
        """绑定账号仓储、密码规则和共享哈希器。"""
        self._repo = repo
        self._config = config
        self._password_manager = (
            password_manager if password_manager is not None else get_password_manager()
        )

    async def list_users(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
    ) -> tuple[list[User], int]:
        """分页列出用户与角色并返回总量。"""
        normalized_query = query.strip() if query is not None else None
        if normalized_query == "":
            normalized_query = None
        users = await self._repo.list_users(
            limit=limit,
            offset=offset,
            query=normalized_query,
        )
        total = await self._repo.count_users(query=normalized_query)
        return users, total

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        doris_role: str | None = None,
        is_admin: bool = False,
    ) -> User:
        """平台管理员创建新用户。"""
        normalized_username = validate_username(username)
        normalized_email = validate_email(email)
        validate_password(password, min_length=self._config.password_min_length)

        normalized_role = normalize_doris_role_name(doris_role) if doris_role else None
        password_hash = await self._password_manager.hash(password)
        now = datetime.now(UTC)
        try:
            async with self._repo.session.begin():
                # 串行化角色存在性和用户名/邮箱唯一性检查，防止并发创建基于过期
                # 快照同时提交。
                await self._repo.lock_security_mutation()
                assigned_role: str | None = None
                if normalized_role is not None:
                    identity = await self._repo.get_query_identity(normalized_role)
                    if identity is None:
                        raise auth_error.RoleNotFoundError
                    assigned_role = normalized_role
                else:
                    default_identity = await self._repo.get_default_query_identity()
                    if default_identity is not None:
                        assigned_role = default_identity.role_name
                if (
                    await self._repo.get_user_by_username(normalized_username)
                    is not None
                ):
                    raise auth_error.UsernameAlreadyExistsError
                if await self._repo.get_user_by_email(normalized_email) is not None:
                    raise auth_error.EmailAlreadyExistsError
                user = User(
                    username=normalized_username,
                    email=normalized_email,
                    password_hash=password_hash,
                    is_active=True,
                    is_admin=is_admin,
                    doris_role_name=assigned_role,
                    created_at=now,
                    updated_at=now,
                )
                return await self._repo.add_user(user)
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError from exc

    async def update_user(
        self,
        user_id: int,
        *,
        username: str | None = None,
        email: str | None = None,
        password: str | None = None,
        doris_role: str | None = None,
        update_doris_role: bool = False,
        is_admin: bool | None = None,
    ) -> User:
        """管理员更新指定用户的基础信息、角色、权限或密码并吊销已有令牌。"""
        if doris_role is not None and not update_doris_role:
            raise ValueError("设置 Doris 角色时必须显式启用角色更新")
        normalized_username: str | None = None
        if username is not None:
            normalized_username = validate_username(username)

        normalized_email: str | None = None
        if email is not None:
            normalized_email = validate_email(email)

        password_hash: str | None = None
        if password is not None:
            validate_password(password, min_length=self._config.password_min_length)
            password_hash = await self._password_manager.hash(password)

        normalized_doris_role: str | None = None
        if update_doris_role and doris_role:
            normalized_doris_role = normalize_doris_role_name(doris_role)

        now = datetime.now(UTC)
        try:
            async with self._repo.session.begin():
                # 角色、最后管理员和唯一性检查与用户更新共享安全锁；刷新令牌也在
                # 同一事务吊销，提交后旧身份立即失效。
                await self._repo.lock_security_mutation()
                if normalized_doris_role is not None:
                    identity_role = await self._repo.get_query_identity(
                        normalized_doris_role
                    )
                    if identity_role is None:
                        raise auth_error.RoleNotFoundError

                user = await self._repo.get_user_by_id(user_id)
                if user is None:
                    raise auth_error.UserNotFoundError

                if (
                    is_admin is not None
                    and user.is_admin
                    and not is_admin
                    and await self._repo.count_admins() <= 1
                ):
                    raise auth_error.LastAdministratorError

                if (
                    normalized_username is not None
                    and normalized_username != user.username
                ):
                    existing = await self._repo.get_user_by_username(
                        normalized_username
                    )
                    if existing is not None and existing.id != user.id:
                        raise auth_error.UsernameAlreadyExistsError

                if normalized_email is not None and normalized_email != user.email:
                    existing_email = await self._repo.get_user_by_email(
                        normalized_email
                    )
                    if existing_email is not None and existing_email.id != user.id:
                        raise auth_error.EmailAlreadyExistsError

                await self._repo.update_user(
                    user,
                    username=normalized_username,
                    email=normalized_email,
                    password_hash=password_hash,
                    doris_role=normalized_doris_role,
                    update_doris_role=update_doris_role,
                    is_admin=is_admin,
                )
                await self._repo.revoke_user_refresh_tokens(user.id, now)
                updated = await self._repo.get_user_by_id(user.id)
                if updated is None:
                    raise RuntimeError("更新后的用户记录无法重新加载")
                return updated
        except IntegrityError as exc:
            raise auth_error.UserAlreadyExistsError from exc
