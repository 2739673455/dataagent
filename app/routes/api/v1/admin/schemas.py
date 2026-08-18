"""管理员接口请求与响应模型"""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.entities.auth import DorisRoleAssetGrant, normalize_doris_role_name
from app.routes.api.v1.auth.schemas import UserResponse
from app.services.doris_permission_service import DorisRoleStatus

_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$"


class DorisRoleResponse(BaseModel):
    """Doris 数据角色响应"""

    name: str
    description: str
    is_default: bool
    query_user: str
    exists_in_doris: bool
    doris_grants: dict[str, Any] | None

    @classmethod
    def from_status(cls, role: DorisRoleStatus) -> Self:
        """从实时角色状态构造响应"""
        return cls(
            name=role.name,
            description=role.description,
            is_default=role.is_default,
            query_user=role.query_user,
            exists_in_doris=role.exists_in_doris,
            doris_grants=role.doris_grants,
        )


class DorisRoleListResponse(BaseModel):
    """Doris 数据角色列表"""

    roles: list[DorisRoleResponse]


class UserListResponse(BaseModel):
    """用户列表响应"""

    users: list[UserResponse]


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
            raise ValueError("columns must be distinct")
        for column in columns:
            if not column or len(column) > 128:
                raise ValueError("invalid column name")
        return columns

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        """校验库、表和列授权层级"""
        if self.table_name is None and self.columns:
            raise ValueError("columns require table_name")
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
