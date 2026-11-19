"""PostgreSQL 查询身份与实时授权模型。"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.identity.contracts import AssetScope
from app.identity.models.base import AuthBase

BUSINESS_PG_ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")


@dataclass(frozen=True, slots=True)
class BusinessPgRowPolicy:
    """PostgreSQL 角色当前生效的行级过滤策略。"""

    policy_name: str
    catalog_name: str
    database_name: str
    table_name: str
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"]
    predicate: str
    managed: bool = False


@dataclass(frozen=True, slots=True)
class BusinessPgSelectGrant:
    """查询账号在当前业务数据库中的一项有效 SELECT 授权。"""

    role_name: str
    data_source: str
    database_name: str
    table_name: str | None = None
    column_name: str | None = None

    @property
    def scope(self) -> str:
        """根据授权目标返回字段、表或数据库级别的资产范围。"""
        if self.column_name is not None:
            return AssetScope.COLUMN.value
        if self.table_name is not None:
            return AssetScope.TABLE.value
        return AssetScope.DATABASE.value


@dataclass(frozen=True, slots=True)
class BusinessPgAuthorizationSnapshot:
    """PostgreSQL 当前有效 SELECT 授权、权限指纹及上级 SELECT 授权标志。"""

    grants: tuple[BusinessPgSelectGrant, ...]
    fingerprint: str
    row_restricted_tables: frozenset[str] = frozenset()


class BusinessPgQueryIdentity(AuthBase):
    """PostgreSQL 数据角色对应的稳定共享查询身份。"""

    __tablename__ = "business_pg_query_identities"

    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    query_user: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    workload_group: Mapped[str] = mapped_column(String(128), nullable=False)
    authorization_fingerprint: Mapped[str | None] = mapped_column(String(64))
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_business_pg_query_identity_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


def normalize_business_pg_role_name(value: str) -> str:
    """校验并规范化 PostgreSQL 角色名。"""
    normalized = value.strip()
    if BUSINESS_PG_ROLE_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("PostgreSQL 角色名称格式无效")
    return normalized
