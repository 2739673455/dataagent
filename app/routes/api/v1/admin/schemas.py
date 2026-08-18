"""管理员接口请求与响应模型"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.entities.auth import PlatformRole, Role, RoleAssetGrant
from app.routes.api.v1.auth.schemas import UserResponse
from app.services.authorization_service import AssetIdentity


class RoleResponse(BaseModel):
    """角色响应"""

    name: PlatformRole
    description: str

    @classmethod
    def from_entity(cls, role: Role) -> Self:
        """从角色实体构造响应"""
        return cls(name=PlatformRole(role.name), description=role.description)


class RoleListResponse(BaseModel):
    """角色列表响应"""

    roles: list[RoleResponse]


class UserListResponse(BaseModel):
    """用户列表响应"""

    users: list[UserResponse]


class SetUserRolesRequest(BaseModel):
    """替换用户角色请求"""

    roles: list[PlatformRole] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def require_distinct_roles(self) -> Self:
        """校验角色列表不重复"""
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("roles must be distinct")
        return self


class AssetGrantRequest(BaseModel):
    """资产白名单授权请求"""

    data_source: str = Field(min_length=1, max_length=256)
    database_name: str | None = Field(default=None, min_length=1, max_length=256)
    table_name: str | None = Field(default=None, min_length=1, max_length=256)
    column_name: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> Self:
        """校验资产层级并清除边缘空白"""
        for field_name in (
            "data_source",
            "database_name",
            "table_name",
            "column_name",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, value.strip())
        self.to_identity()
        return self

    def to_identity(self) -> AssetIdentity:
        """转换为资产领域标识"""
        return AssetIdentity(
            data_source=self.data_source,
            database_name=self.database_name,
            table_name=self.table_name,
            column_name=self.column_name,
        )


class AssetGrantResponse(BaseModel):
    """资产白名单授权响应"""

    id: UUID
    role: PlatformRole
    scope: str
    data_source: str
    database_name: str | None
    table_name: str | None
    column_name: str | None
    created_at: datetime

    @classmethod
    def from_entity(cls, grant: RoleAssetGrant) -> Self:
        """从授权实体构造响应"""
        return cls(
            id=grant.id,
            role=PlatformRole(grant.role_name),
            scope=grant.scope,
            data_source=grant.data_source,
            database_name=grant.database_name,
            table_name=grant.table_name,
            column_name=grant.column_name,
            created_at=grant.created_at,
        )


class AssetGrantListResponse(BaseModel):
    """资产授权列表响应"""

    grants: list[AssetGrantResponse]
