"""Doris 查询账号存储与连接凭据模型。"""

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
class ResolvedQueryPrincipal:
    """一次查询使用的角色、账号和明文密码。"""

    role_name: str
    query_user: str
    password: str = field(repr=False)
