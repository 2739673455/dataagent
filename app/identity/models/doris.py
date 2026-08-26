"""Doris 查询身份与权限投影模型"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AuthBase

DORIS_ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def normalize_doris_role_name(value: str) -> str:
    """校验并规范化 Doris 角色名"""
    normalized = value.strip()
    if DORIS_ROLE_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Doris 角色名称格式无效")
    return normalized


def asset_resource_key(
    data_source: str,
    database_name: str | None = None,
    table_name: str | None = None,
    column_name: str | None = None,
) -> str:
    """生成层级数据资产的稳定资源键"""
    canonical = json.dumps(
        [data_source, database_name, table_name, column_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class AssetScope(StrEnum):
    """数据资产授权粒度"""

    DATA_SOURCE = "data_source"
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"


@dataclass(frozen=True, slots=True)
class DorisRowPolicy:
    """Doris 角色当前生效的行级过滤策略"""

    policy_name: str
    catalog_name: str
    database_name: str
    table_name: str
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"]
    predicate: str


class DorisQueryIdentity(AuthBase):
    """Doris 数据角色对应的稳定共享查询身份"""

    __tablename__ = "doris_query_identities"

    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    query_user: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    workload_group: Mapped[str] = mapped_column(String(128), nullable=False)
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
            "uq_doris_query_identity_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class DorisRoleAssetGrant(AuthBase):
    """Doris 角色 SELECT 权限的应用侧可见性投影"""

    __tablename__ = "doris_role_asset_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_name: Mapped[str] = mapped_column(
        ForeignKey("doris_query_identities.role_name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    data_source: Mapped[str] = mapped_column(String(256), nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(256))
    table_name: Mapped[str | None] = mapped_column(String(256))
    column_name: Mapped[str | None] = mapped_column(String(256))
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "role_name",
            "scope",
            "resource_key",
            name="uq_doris_role_asset_grant_resource",
        ),
        CheckConstraint(
            "(scope = 'data_source' AND database_name IS NULL "
            "AND table_name IS NULL AND column_name IS NULL) OR "
            "(scope = 'database' AND database_name IS NOT NULL "
            "AND table_name IS NULL AND column_name IS NULL) OR "
            "(scope = 'table' AND database_name IS NOT NULL "
            "AND table_name IS NOT NULL AND column_name IS NULL) OR "
            "(scope = 'column' AND database_name IS NOT NULL "
            "AND table_name IS NOT NULL AND column_name IS NOT NULL)",
            name="ck_doris_role_asset_grant_hierarchy",
        ),
    )
