"""管理员接口请求与响应模型"""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.identity.api.auth.schemas import UserResponse
from app.identity.models.doris import (
    DorisQueryIdentity,
    DorisRoleAssetGrant,
    DorisRowPolicy,
    normalize_doris_role_name,
)
from app.identity.services.account_validation import (
    EMAIL_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    validate_email,
    validate_username,
)
from app.identity.services.doris_permission import DorisRoleStatus
from app.shared.contracts.doris import (
    DORIS_IDENTIFIER_PATTERN,
    DORIS_WORKLOAD_GROUP_PATTERN,
)


class CreateUserRequest(BaseModel):
    """管理员创建用户请求"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
    )
    email: str = Field(min_length=3, max_length=EMAIL_MAX_LENGTH)
    password: SecretStr = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    doris_role: str | None = Field(default=None, max_length=64)
    is_admin: bool = False

    @field_validator("username")
    @classmethod
    def normalize_username(cls, username: str) -> str:
        """规范化并校验用户名"""
        return validate_username(username)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        """规范化并校验邮箱"""
        return validate_email(email)

    @field_validator("doris_role")
    @classmethod
    def normalize_role(cls, role: str | None) -> str | None:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role) if role else None


class UpdateUserRequest(BaseModel):
    """管理员更新用户信息请求"""

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
    )
    email: str | None = Field(default=None, min_length=3, max_length=EMAIL_MAX_LENGTH)
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
    )
    doris_role: str | None = Field(default=None)
    is_admin: bool | None = Field(default=None)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, username: str | None) -> str | None:
        """规范化并校验用户名"""
        if username is None:
            return None
        return validate_username(username)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str | None) -> str | None:
        """规范化并校验邮箱"""
        if email is None:
            return None
        return validate_email(email)

    @field_validator("doris_role")
    @classmethod
    def normalize_role(cls, role: str | None) -> str | None:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role) if role else None

    @model_validator(mode="after")
    def validate_updates(self) -> Self:
        """要求至少更新一个字段，并限制空值只用于清除 Doris 角色"""
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个待更新字段")

        nullable_updates = {"username", "email", "password", "is_admin"}
        null_fields = [
            field_name
            for field_name in nullable_updates & self.model_fields_set
            if getattr(self, field_name) is None
        ]
        if null_fields:
            raise ValueError(f"更新字段不能为 null: {', '.join(sorted(null_fields))}")
        return self


class DorisRoleResponse(BaseModel):
    """Doris 数据角色响应"""

    name: str
    description: str
    is_default: bool
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
            query_user=identity.query_user,
            workload_group=identity.workload_group,
            exists_in_doris=True,
            doris_grants=None,
        )


class DorisRoleListResponse(BaseModel):
    """Doris 数据角色列表"""

    roles: list[DorisRoleResponse]


class DorisWorkloadGroupListResponse(BaseModel):
    """Doris 工作组列表响应"""

    workload_groups: list[str]


class DorisExistingRoleResponse(BaseModel):
    """Doris 已有角色响应"""

    name: str
    managed: bool
    doris_users: list[str]


class DorisExistingRoleListResponse(BaseModel):
    """Doris 已有角色列表响应"""

    roles: list[DorisExistingRoleResponse]


class CreateDorisRoleRequest(BaseModel):
    """创建 Doris 角色及稳定查询身份请求"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=256)
    query_user: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
    )
    workload_group: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_WORKLOAD_GROUP_PATTERN,
    )

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

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=64)

    @field_validator("role")
    @classmethod
    def normalize_role(cls, role: str) -> str:
        """校验 Doris 角色名"""
        return normalize_doris_role_name(role)


class SetUserAdministratorRequest(BaseModel):
    """设置平台管理员请求"""

    model_config = ConfigDict(extra="forbid")

    is_admin: bool


class SelectGrantRequest(BaseModel):
    """Doris SELECT 授权或回收请求"""

    model_config = ConfigDict(extra="forbid")

    table_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
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

    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
    )
    table_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
    )
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"] = "RESTRICTIVE"
    predicate: str = Field(min_length=1, max_length=4096)


class DropRowPolicyRequest(BaseModel):
    """删除 Doris 行策略请求"""

    model_config = ConfigDict(extra="forbid")

    policy_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
    )
    table_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=DORIS_IDENTIFIER_PATTERN,
    )


class RowPolicyResponse(BaseModel):
    """Doris 实时行策略"""

    policy_name: str
    catalog_name: str
    database_name: str
    table_name: str
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"]
    predicate: str

    @classmethod
    def from_model(cls, policy: DorisRowPolicy) -> Self:
        """从行策略模型构造响应"""
        return cls(
            policy_name=policy.policy_name,
            catalog_name=policy.catalog_name,
            database_name=policy.database_name,
            table_name=policy.table_name,
            policy_type=policy.policy_type,
            predicate=policy.predicate,
        )


class RowPolicyListResponse(BaseModel):
    """Doris 实时行策略列表"""

    policies: list[RowPolicyResponse]
