"""Doris 查询身份与实时授权模型。"""

from dataclasses import dataclass, field

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import AuthBase


class DorisQueryIdentity(AuthBase):
    """Doris 角色关联的查询账号、凭据和工作组。"""

    __tablename__ = "doris_query_identities"

    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    query_user: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    workload_group: Mapped[str] = mapped_column(String(128), nullable=False)


@dataclass(frozen=True, slots=True)
class DorisSelectGrant:
    """业务库内的一项 SELECT 授权，未指定表或字段表示对应整级授权。"""

    role_name: str
    data_source: str
    database_name: str
    table_name: str | None = None
    column_name: str | None = None


@dataclass(frozen=True, slots=True)
class DorisAuthorizationSnapshot:
    """同次权限读取的业务库 SELECT 授权。"""

    grants: tuple[DorisSelectGrant, ...]


@dataclass(frozen=True, slots=True)
class ResolvedQueryPrincipal:
    """服务端为一次查询解析出的 Doris 身份。"""

    role_name: str
    query_user: str
    password: str = field(repr=False)
