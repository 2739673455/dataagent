"""Admin 模块应用层 DTO — 冻结 dataclass，从 use case 返回给 presentation 层"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UserInfoResult:
    """用户基本信息"""

    id: int
    email: str
    username: str
    yn: int
    effective: int = 1
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class RoleInfoResult:
    """角色基本信息"""

    id: int
    name: str
    yn: int
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionInfoResult:
    """权限基本信息"""

    id: int
    name: str
    description: str | None = None
    yn: int = 1
    effective: int = 1
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class UserDetailResult:
    """用户详情（含角色和权限列表）"""

    id: int
    email: str
    username: str
    yn: int
    roles: list[RoleInfoResult] = field(default_factory=list)
    permissions: list[PermissionInfoResult] = field(default_factory=list)
    effective: int = 1
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class UserListResult:
    """用户列表（分页）"""

    total: int
    items: list[UserInfoResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RoleDetailResult:
    """角色详情（含用户和权限列表）"""

    id: int
    name: str
    yn: int
    users: list[UserInfoResult] = field(default_factory=list)
    permissions: list[PermissionInfoResult] = field(default_factory=list)
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class RoleListResult:
    """角色列表（分页）"""

    total: int
    items: list[RoleInfoResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PermissionDetailResult:
    """权限详情（含角色和用户列表）"""

    id: int
    name: str
    yn: int
    roles: list[RoleInfoResult] = field(default_factory=list)
    users: list[UserInfoResult] = field(default_factory=list)
    description: str | None = None
    effective: int = 1
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionListResult:
    """权限列表（分页）"""

    total: int
    items: list[PermissionInfoResult] = field(default_factory=list)
