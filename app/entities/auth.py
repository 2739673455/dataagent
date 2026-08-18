"""认证与资产授权实体"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.entities.base import Base


class PlatformRole(StrEnum):
    """平台内置角色"""

    ADMIN = "Admin"
    ANALYST = "Analyst"
    VIEWER = "Viewer"


BASE_PLATFORM_ROLES = tuple(PlatformRole)


class AssetScope(StrEnum):
    """数据资产授权粒度"""

    DATA_SOURCE = "data_source"
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"


class User(Base):
    """平台用户"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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

    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles",
        lazy="selectin",
        back_populates="users",
    )

    @property
    def role_names(self) -> frozenset[PlatformRole]:
        """返回用户的内置角色集合"""
        return frozenset(PlatformRole(role.name) for role in self.roles)


class Role(Base):
    """平台角色"""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    users: Mapped[list[User]] = relationship(
        secondary="user_roles",
        lazy="selectin",
        back_populates="roles",
    )


class UserRole(Base):
    """用户角色关联"""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RefreshToken(Base):
    """可轮换的刷新令牌记录"""

    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_refresh_tokens_family_active", "family_id", "revoked_at"),
    )


class RoleAssetGrant(Base):
    """角色的数据资产白名单授权"""

    __tablename__ = "role_asset_grants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"),
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
            name="uq_role_asset_grant_resource",
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
            name="ck_role_asset_grant_hierarchy",
        ),
    )
