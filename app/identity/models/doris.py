"""Doris 查询身份与实时授权模型。"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
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

from app.shared.database.base import AuthBase

DORIS_ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def normalize_doris_role_name(value: str) -> str:
    """校验并规范化 Doris 角色名。"""
    normalized = value.strip()
    if DORIS_ROLE_NAME_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Doris 角色名称格式无效")
    return normalized


class AssetScope(StrEnum):
    """数据资产授权粒度。"""

    DATA_SOURCE = "data_source"
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"


@dataclass(frozen=True, slots=True)
class DorisRowPolicy:
    """Doris 角色当前生效的行级过滤策略。"""

    policy_name: str
    catalog_name: str
    database_name: str
    table_name: str
    policy_type: Literal["RESTRICTIVE", "PERMISSIVE"]
    predicate: str


class DorisQueryIdentity(AuthBase):
    """Doris 数据角色对应的稳定共享查询身份。"""

    __tablename__ = "doris_query_identities"

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
            "uq_doris_query_identity_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


@dataclass(frozen=True, slots=True)
class DorisSelectGrant:
    """查询账号在当前业务数据库中的一项有效 SELECT 授权。"""

    role_name: str
    data_source: str
    database_name: str
    table_name: str | None = None
    column_name: str | None = None

    @property
    def scope(self) -> str:
        if self.column_name is not None:
            return AssetScope.COLUMN.value
        if self.table_name is not None:
            return AssetScope.TABLE.value
        return AssetScope.DATABASE.value


@dataclass(frozen=True, slots=True)
class DorisAuthorizationSnapshot:
    grants: tuple[DorisSelectGrant, ...]
    fingerprint: str
    # 全局或 Catalog SELECT 不能通过当前数据库的撤权入口消除。
    has_broad_select: bool = False
