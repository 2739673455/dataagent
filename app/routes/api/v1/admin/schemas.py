"""管理员接口请求与响应模型"""

import re
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.models.auth import (
    DorisQueryIdentity,
    DorisRoleAssetGrant,
    normalize_doris_role_name,
)
from app.routes.api.v1.auth.schemas import UserResponse
from app.services.doris_permission_service import DorisRoleStatus

_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$"
_USERNAME_PATTERN = r"^[a-z0-9_.-]{3,64}$"
_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


class CreateUserRequest(BaseModel):
    """管理员创建用户请求"""

    username: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=6, max_length=128)
    doris_role: str | None = Field(default=None, max_length=64)
    is_admin: bool = False

    @field_validator("username")
    @classmethod
    def validate_username(cls, username: str) -> str:
        """规范化并校验用户名"""
        normalized = username.strip().casefold()
        if not re.match(_USERNAME_PATTERN, normalized):
            raise ValueError("用户名只能包含小写字母、数字、点、下划线和连字符")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, email: str) -> str:
        """规范化并校验邮箱"""
        normalized = email.strip().casefold()
        if not re.match(_EMAIL_PATTERN, normalized):
            raise ValueError("邮箱地址格式无效")
        return normalized

    @field_validator("doris_role")
    @classmethod
    def normalize_role(cls, role: str | None) -> str | None:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role) if role else None


class DorisRoleResponse(BaseModel):
    """Doris 数据角色响应"""

    name: str
    description: str
    is_default: bool
    is_active: bool
    query_user: str
    workload_group: str
    exists_in_doris: bool
    doris_grants: dict[str, Any] | None

    @classmethod
    def from_status(cls, role: DorisRoleStatus) -> Self:
        """从实时角色状态构造响应"""
        return cls(
            name=role.name,
            description=role.description,
            is_default=role.is_default,
            is_active=role.is_active,
            query_user=role.query_user,
            workload_group=role.workload_group,
            exists_in_doris=role.exists_in_doris,
            doris_grants=role.doris_grants,
        )

    @classmethod
    def from_entity(cls, identity: DorisQueryIdentity) -> Self:
        """从持久化查询身份构造响应"""
        return cls(
            name=identity.role_name,
            description=identity.description,
            is_default=identity.is_default,
            is_active=identity.is_active,
            query_user=identity.query_user,
            workload_group=identity.workload_group,
            exists_in_doris=True,
            doris_grants=None,
        )


class DorisRoleListResponse(BaseModel):
    """Doris 数据角色列表"""

    roles: list[DorisRoleResponse]


class DiscoveredDorisRoleResponse(BaseModel):
    """Doris 原生角色发现响应"""

    name: str
    is_attached: bool
    description: str | None
    query_user: str | None
    workload_group: str | None


class DiscoveredDorisRoleListResponse(BaseModel):
    """Doris 原生角色发现列表响应"""

    roles: list[DiscoveredDorisRoleResponse]


class AttachDorisRoleRequest(BaseModel):
    """接入已有 Doris 角色请求"""

    role: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=256)
    workload_group: str = Field(
        default="normal",
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    query_user: str | None = Field(
        default=None,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    is_default: bool = False

    @field_validator("role")
    @classmethod
    def normalize_role(cls, role: str) -> str:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, description: str) -> str:
        """规范化角色说明"""
        normalized = description.strip()
        if not normalized:
            raise ValueError("角色描述不能为空")
        return normalized


class CreateDorisRoleRequest(BaseModel):
    """创建 Doris 角色及稳定查询身份请求"""

    role: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=256)
    query_user: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    workload_group: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    is_default: bool = False

    @field_validator("role")
    @classmethod
    def normalize_role(cls, role: str) -> str:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, description: str) -> str:
        """规范化角色说明"""
        normalized = description.strip()
        if not normalized:
            raise ValueError("角色描述不能为空")
        return normalized


class UserListResponse(BaseModel):
    """用户列表响应"""

    users: list[UserResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    has_more: bool


class SetUserDorisRoleRequest(BaseModel):
    """设置用户唯一 Doris 角色请求"""

    role: str = Field(min_length=1, max_length=64)

    @field_validator("role")
    @classmethod
    def normalize_role(cls, role: str) -> str:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role)


class SetUserAdministratorRequest(BaseModel):
    """设置平台管理员请求"""

    is_admin: bool


class SelectGrantRequest(BaseModel):
    """Doris SELECT 授权或回收请求"""

    table_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    columns: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("columns")
    @classmethod
    def validate_columns(cls, columns: list[str]) -> list[str]:
        """校验列名且拒绝重复"""
        if len(set(columns)) != len(columns):
            raise ValueError("列名不能重复")
        for column in columns:
            if not column or len(column) > 128:
                raise ValueError("列名格式无效")
        return columns

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        """校验库、表和列授权层级"""
        if self.table_name is None and self.columns:
            raise ValueError("指定列权限时必须提供表名")
        return self


class AssetGrantResponse(BaseModel):
    """Doris SELECT 权限投影响应"""

    id: UUID
    role: str
    scope: str
    data_source: str
    database_name: str | None
    table_name: str | None
    column_name: str | None
    created_at: datetime

    @classmethod
    def from_entity(cls, grant: DorisRoleAssetGrant) -> Self:
        """从权限投影实体构造响应"""
        return cls(
            id=grant.id,
            role=grant.role_name,
            scope=grant.scope,
            data_source=grant.data_source,
            database_name=grant.database_name,
            table_name=grant.table_name,
            column_name=grant.column_name,
            created_at=grant.created_at,
        )


class AssetGrantListResponse(BaseModel):
    """Doris SELECT 权限投影列表"""

    grants: list[AssetGrantResponse]


class RowPolicyRequest(BaseModel):
    """创建 Doris 行策略请求"""

    policy_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    table_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"] = "RESTRICTIVE"
    predicate: str = Field(min_length=1, max_length=4096)


class DropRowPolicyRequest(BaseModel):
    """删除 Doris 行策略请求"""

    policy_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )
    table_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER_PATTERN,
    )


class RowPolicyListResponse(BaseModel):
    """Doris 实时行策略列表"""

    policies: list[dict[str, Any]]
