"""管理接口请求和响应模型"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, EmailStr, Field, field_validator

if TYPE_CHECKING:
    from app.domain.entities import Permission, Role, User


def _require_user_id(user_id: int | None) -> int:
    """防御性检查：确保 user.id 非空"""
    if user_id is None:
        raise RuntimeError("user.id should not be None")
    return user_id


def _require_role_id(role_id: int | None) -> int:
    """防御性检查：确保 role.id 非空"""
    if role_id is None:
        raise RuntimeError("role.id should not be None")
    return role_id


def _require_permission_id(permission_id: int | None) -> int:
    """防御性检查：确保 permission.id 非空"""
    if permission_id is None:
        raise RuntimeError("permission.id should not be None")
    return permission_id


class UserInfo(BaseModel):
    id: int = Field(..., description="用户ID")
    email: str = Field(..., description="邮箱")
    username: str = Field(..., description="用户名")
    yn: int = Field(..., description="是否启用")
    effective: int = Field(default=1, description="当前上下文下是否生效")
    created_at: str | None = Field(..., description="创建时间")

    @classmethod
    def from_user(cls, user: "User", effective: int = 1) -> "UserInfo":
        """从 User 实体构造 UserInfo"""
        return cls(
            id=_require_user_id(user.id),
            email=user.email,
            username=user.name,
            yn=user.yn,
            effective=effective,
            created_at=user.created_at,
        )


class RoleInfo(BaseModel):
    id: int = Field(..., description="角色ID")
    name: str = Field(..., description="角色名")
    yn: int = Field(..., description="是否启用")
    created_at: str | None = Field(default=None, description="创建时间")

    @classmethod
    def from_role(cls, role: "Role") -> "RoleInfo":
        """从 Role 实体构造 RoleInfo"""
        return cls(
            id=_require_role_id(role.id),
            name=role.name,
            yn=role.yn,
            created_at=role.created_at,
        )


class PermissionInfo(BaseModel):
    id: int = Field(..., description="权限ID")
    name: str = Field(..., description="权限名")
    description: str | None = Field(default=None, description="权限描述")
    yn: int = Field(..., description="是否启用")
    effective: int = Field(default=1, description="当前上下文下是否生效")
    created_at: str | None = Field(default=None, description="创建时间")

    @classmethod
    def from_permission(cls, permission: "Permission", effective: int = 1) -> "PermissionInfo":
        """从 Permission 实体构造 PermissionInfo"""
        return cls(
            id=_require_permission_id(permission.id),
            name=permission.name,
            description=permission.description,
            yn=permission.yn,
            effective=effective,
            created_at=permission.created_at,
        )


class CreateUserRequest(BaseModel):
    email: EmailStr = Field(..., description="邮箱")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """校验用户名：不能包含 @，长度 1-50"""
        v = v.strip()
        if "@" in v:
            raise ValueError("用户名不能包含@字符")
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """校验密码：长度 6-128"""
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class UpdateUserRequest(BaseModel):
    user_id: int = Field(..., description="用户ID")
    email: EmailStr | None = Field(default=None, description="邮箱")
    username: str | None = Field(default=None, description="用户名")
    password: str | None = Field(default=None, description="密码")
    yn: int | None = Field(default=None, description="是否启用: 1-启用, 0-禁用")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        """校验用户名（可选）"""
        if v is None:
            return v
        v = v.strip()
        if "@" in v:
            raise ValueError("用户名不能包含@字符")
        if len(v) < 1:
            raise ValueError("用户名不少于1个字符")
        if len(v) > 50:
            raise ValueError("用户名不超过50个字符")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        """校验密码（可选）：长度 6-128"""
        if v is None:
            return v
        if len(v) < 6:
            raise ValueError("密码不少于6个字符")
        if len(v) > 128:
            raise ValueError("密码不超过128个字符")
        return v


class RemoveUserRequest(BaseModel):
    user_id: int = Field(..., description="用户ID")


class UserDetailResponse(UserInfo):
    roles: list[RoleInfo] = Field(default=[], description="所属角色列表")
    permissions: list[PermissionInfo] = Field(default=[], description="拥有的权限列表")


class UserListResponse(BaseModel):
    total: int = Field(..., description="总数")
    items: list[UserInfo] = Field(..., description="用户列表")


class CreateRoleRequest(BaseModel):
    name: str = Field(..., description="角色名")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """校验角色名：长度 1-100"""
        v = v.strip()
        if len(v) < 1:
            raise ValueError("角色名不少于1个字符")
        if len(v) > 100:
            raise ValueError("角色名不超过100个字符")
        return v


class UpdateRoleRequest(BaseModel):
    role_id: int = Field(..., description="角色ID")
    name: str | None = Field(default=None, description="角色名")
    yn: int | None = Field(default=None, description="是否启用")


class RemoveRoleRequest(BaseModel):
    role_id: int = Field(..., description="角色ID")


class RoleDetailResponse(RoleInfo):
    users: list[UserInfo] = Field(default=[], description="角色内用户列表")
    permissions: list[PermissionInfo] = Field(default=[], description="角色权限列表")


class RoleListResponse(BaseModel):
    total: int = Field(..., description="总数")
    items: list[RoleInfo] = Field(..., description="角色列表")


class CreatePermissionRequest(BaseModel):
    name: str = Field(..., description="权限名")
    description: str | None = Field(default=None, description="权限描述")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """校验权限名：长度 1-100"""
        v = v.strip()
        if len(v) < 1:
            raise ValueError("权限名不少于1个字符")
        if len(v) > 100:
            raise ValueError("权限名不超过100个字符")
        return v


class UpdatePermissionRequest(BaseModel):
    permission_id: int = Field(..., description="权限ID")
    name: str | None = Field(default=None, description="权限名")
    description: str | None = Field(default=None, description="权限描述")
    yn: int | None = Field(default=None, description="是否启用")


class RemovePermissionRequest(BaseModel):
    permission_id: int = Field(..., description="权限ID")


class PermissionDetailResponse(PermissionInfo):
    roles: list[RoleInfo] = Field(default=[], description="拥有此权限的角色列表")
    users: list[UserInfo] = Field(default=[], description="拥有此权限的用户列表")


class PermissionListResponse(BaseModel):
    total: int = Field(..., description="总数")
    items: list[PermissionInfo] = Field(..., description="权限列表")


class UserRoleRelation(BaseModel):
    user_id: int = Field(..., description="用户ID")
    role_id: int = Field(..., description="角色ID")


class RolePermissionRelation(BaseModel):
    role_id: int = Field(..., description="角色ID")
    permission_id: int = Field(..., description="权限ID")


class BatchAddUserRoleRequest(BaseModel):
    relations: list[UserRoleRelation] = Field(..., description="关联关系列表")


class BatchRemoveUserRoleRequest(BaseModel):
    relations: list[UserRoleRelation] = Field(..., description="关联关系列表")


class BatchAddRolePermissionRequest(BaseModel):
    relations: list[RolePermissionRelation] = Field(..., description="关联关系列表")


class BatchRemoveRolePermissionRequest(BaseModel):
    relations: list[RolePermissionRelation] = Field(..., description="关联关系列表")
