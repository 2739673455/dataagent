"""领域实体"""

from dataclasses import dataclass


@dataclass
class User:
    """用户"""

    id: int | None
    email: str
    name: str
    password_hash: str
    yn: int
    created_at: str
    updated_at: str


@dataclass
class Role:
    """角色"""

    id: int | None
    name: str
    yn: int
    created_at: str
    updated_at: str


@dataclass
class Permission:
    """权限"""

    id: int | None
    name: str
    description: str | None
    yn: int
    created_at: str
    updated_at: str


@dataclass
class Session:
    """用户会话"""

    session_id: str
    user_id: int
    created_at: str
    expires_at: str
    revoked_at: str | None


@dataclass
class AccessToken:
    """访问令牌"""

    access_token: str
    user_id: int
    session_id: str
    client_id: str
    scope: str | None
    created_at: str
    expires_at: str
    revoked_at: str | None


@dataclass
class AuthCode:
    """OAuth 授权码"""

    code: str
    user_id: int
    session_id: str
    client_id: str
    redirect_uri: str
    state: str
    code_challenge: str
    code_challenge_method: str
    created_at: str
    expires_at: str
    used_at: str | None


@dataclass
class EmailCode:
    """邮箱验证码"""

    email: str
    code_type: str
    code: str
    created_at: str
    expires_at: str
    used_at: str | None


@dataclass
class RoleWithPermissions(Role):
    """角色及其权限"""

    permissions: list[Permission]


@dataclass
class UserWithRoles(User):
    """用户及其角色"""

    roles: list[Role]


@dataclass
class UserWithRolePermissions(User):
    """用户及其角色与权限"""

    roles: list[RoleWithPermissions]


@dataclass
class RoleWithUsers(Role):
    """角色及其用户"""

    users: list[User]


@dataclass
class RoleWithUserPermissions(Role):
    """角色及其用户与权限"""

    users: list[User]
    permissions: list[Permission]


@dataclass
class PermissionWithRoles(Permission):
    """权限及其角色"""

    roles: list[Role]


@dataclass
class PermissionWithRoleUsers(Permission):
    """权限及其角色与用户"""

    roles: list[RoleWithUsers]
