"""领域端口 — 仓库与服务抽象"""

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from .entities import (
    AccessToken,
    AuthCode,
    Permission,
    PermissionWithRoleUsers,
    Role,
    RoleWithUserPermissions,
    RoleWithUsers,
    Session,
    User,
    UserWithRolePermissions,
    UserWithRoles,
)


class UserRepository(ABC):
    """用户仓库"""

    @abstractmethod
    async def create(
        self, db: AsyncSession, email: str, username: str, password: str
    ) -> User: ...

    @abstractmethod
    async def remove(self, db: AsyncSession, user_id: int) -> None: ...

    @abstractmethod
    async def update(
        self,
        db: AsyncSession,
        user: User,
        *,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
        yn: int | None = None,
    ) -> User: ...

    @abstractmethod
    async def get_by_id(self, db: AsyncSession, user_id: int) -> User | None: ...

    @abstractmethod
    async def get_by_id_with_role(
        self, db: AsyncSession, user_id: int
    ) -> UserWithRoles | None: ...

    @abstractmethod
    async def get_by_id_with_role_permission(
        self, db: AsyncSession, user_id: int
    ) -> UserWithRolePermissions | None: ...

    @abstractmethod
    async def get_by_email(self, db: AsyncSession, email: str) -> User | None: ...

    @abstractmethod
    async def get_by_email_with_role(
        self, db: AsyncSession, email: str
    ) -> UserWithRoles | None: ...

    @abstractmethod
    async def get_by_email_with_role_permission(
        self, db: AsyncSession, email: str
    ) -> UserWithRolePermissions | None: ...

    @abstractmethod
    async def ls(
        self,
        db: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[User], int]: ...


class TokenRepository(ABC):
    """令牌仓库"""

    @abstractmethod
    async def create(
        self,
        db: AsyncSession,
        user_id: int,
        session_id: str,
        access_token: str,
        client_id: str,
        expire_seconds: int,
        scopes: list[str],
    ) -> None: ...

    @abstractmethod
    async def remove(self, db: AsyncSession, access_token: str) -> None: ...

    @abstractmethod
    async def remove_all_by_user(self, db: AsyncSession, user_id: int) -> None: ...

    @abstractmethod
    async def remove_all_by_session(
        self, db: AsyncSession, session_id: str
    ) -> None: ...

    @abstractmethod
    async def get_active(
        self, db: AsyncSession, access_token: str
    ) -> AccessToken | None: ...

    @abstractmethod
    async def update_all_by_user(
        self, db: AsyncSession, user_id: int, scopes: list[str]
    ) -> None: ...


class SessionRepository(ABC):
    """会话仓库"""

    @abstractmethod
    async def create(
        self, db: AsyncSession, session_id: str, user_id: int, expire_seconds: int
    ) -> None: ...

    @abstractmethod
    async def remove(self, db: AsyncSession, session_id: str) -> None: ...

    @abstractmethod
    async def remove_all_by_user(self, db: AsyncSession, user_id: int) -> None: ...

    @abstractmethod
    async def get_and_refresh(
        self, db: AsyncSession, session_id: str, expire_seconds: int
    ) -> Session | None: ...


class AuthCodeRepository(ABC):
    """OAuth 授权码仓库"""

    @abstractmethod
    async def create(
        self,
        db: AsyncSession,
        *,
        code: str,
        user_id: int,
        session_id: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
        expire_seconds: int,
    ) -> None: ...

    @abstractmethod
    async def get_active(self, db: AsyncSession, code: str) -> AuthCode | None: ...

    @abstractmethod
    async def mark_used(self, db: AsyncSession, code: str) -> None: ...


class EmailCodeRepository(ABC):
    """邮箱验证码仓库"""

    @abstractmethod
    async def create(
        self,
        db: AsyncSession,
        email: str,
        code_type: str,
        code: str,
        expire_seconds: int,
    ) -> None: ...

    @abstractmethod
    async def consume(
        self, db: AsyncSession, email: str, code_type: str, code: str
    ) -> bool: ...


class EmailSender(ABC):
    """邮件发送服务"""

    @abstractmethod
    async def send_verification_code(
        self, to: str, code: str, code_type: str
    ) -> None: ...


class PasswordHasher(ABC):
    """密码哈希服务"""

    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def verify(self, password: str, password_hash: str) -> bool: ...


class TokenFactory(ABC):
    """令牌生成工厂"""

    @abstractmethod
    def session_id(self) -> str: ...

    @abstractmethod
    def authorization_code(self) -> str: ...

    @abstractmethod
    def access_token(self) -> str: ...

    @abstractmethod
    def verification_code(self) -> str: ...


class RoleRepository(ABC):
    """角色仓库"""

    @abstractmethod
    async def create(self, db: AsyncSession, name: str) -> Role: ...

    @abstractmethod
    async def remove(self, db: AsyncSession, role_id: int) -> None: ...

    @abstractmethod
    async def update(
        self,
        db: AsyncSession,
        role: Role,
        *,
        name: str | None = None,
        yn: int | None = None,
    ) -> Role: ...

    @abstractmethod
    async def get_by_id(self, db: AsyncSession, role_id: int) -> Role | None: ...

    @abstractmethod
    async def get_by_name(self, db: AsyncSession, name: str) -> Role | None: ...

    @abstractmethod
    async def get_by_id_with_user(
        self, db: AsyncSession, role_id: int
    ) -> RoleWithUsers | None: ...

    @abstractmethod
    async def get_by_id_with_user_permission(
        self, db: AsyncSession, role_id: int
    ) -> RoleWithUserPermissions | None: ...

    @abstractmethod
    async def ls(
        self,
        db: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[Role], int]: ...


class PermissionRepository(ABC):
    """权限仓库"""

    @abstractmethod
    async def create(
        self, db: AsyncSession, name: str, description: str | None = None
    ) -> Permission: ...

    @abstractmethod
    async def remove(self, db: AsyncSession, permission_id: int) -> None: ...

    @abstractmethod
    async def update(
        self,
        db: AsyncSession,
        permission: Permission,
        *,
        name: str | None = None,
        description: str | None = None,
        yn: int | None = None,
    ) -> Permission: ...

    @abstractmethod
    async def get_by_id(
        self, db: AsyncSession, permission_id: int
    ) -> Permission | None: ...

    @abstractmethod
    async def get_by_name(self, db: AsyncSession, name: str) -> Permission | None: ...

    @abstractmethod
    async def get_by_id_with_role_user(
        self, db: AsyncSession, permission_id: int
    ) -> PermissionWithRoleUsers | None: ...

    @abstractmethod
    async def ls(
        self,
        db: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[Permission], int]: ...


class RelationRepository(ABC):
    """关联关系仓库"""

    @abstractmethod
    async def add_user_role(
        self, db: AsyncSession, pairs: list[tuple[int, int]]
    ) -> None: ...

    @abstractmethod
    async def remove_user_role(
        self, db: AsyncSession, pairs: list[tuple[int, int]]
    ) -> None: ...

    @abstractmethod
    async def add_role_permission(
        self, db: AsyncSession, pairs: list[tuple[int, int]]
    ) -> None: ...

    @abstractmethod
    async def remove_role_permission(
        self, db: AsyncSession, pairs: list[tuple[int, int]]
    ) -> None: ...


class PkceService(ABC):
    """PKCE 验证服务"""

    @abstractmethod
    def validate_base64url_43(self, value: str) -> bool: ...

    @abstractmethod
    def create_code_challenge(self, code_verifier: str) -> str: ...
