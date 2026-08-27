"""查询经验聚合与检索模型"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database.base import MetaBase


class QueryExperience(MetaBase):
    """按 SQL 结构聚合的用户私有查询经验"""

    __tablename__ = "query_experiences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    representative_sql: Mapped[str] = mapped_column(Text, nullable=False)
    sql_template: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="candidate",
        server_default="candidate",
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    adopted_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    indexed_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    first_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    assets: Mapped[list["QueryExperienceAsset"]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "role_name",
            "fingerprint",
            name="uq_query_experience_owner_role_fingerprint",
        ),
        CheckConstraint(
            "quality IN ('candidate', 'promoted', 'disabled')",
            name="ck_query_experience_quality",
        ),
        CheckConstraint(
            "success_count > 0 AND adopted_count >= 0",
            name="ck_query_experience_counts",
        ),
        CheckConstraint(
            "revision > 0 AND indexed_revision >= 0",
            name="ck_query_experience_revisions",
        ),
        Index(
            "ix_query_experience_owner_role_last_used",
            "owner_user_id",
            "role_name",
            "last_used_at",
        ),
    )

    def refresh_from_success(
        self,
        *,
        purpose: str,
        representative_sql: str,
        sql_template: str,
        used_at: datetime,
    ) -> None:
        """记录成功复用或重建经验"""
        if purpose not in self.purposes:
            self.purposes = [*self.purposes, purpose]
        self.representative_sql = representative_sql
        self.sql_template = sql_template
        self.success_count += 1
        self.revision += 1
        self.last_used_at = used_at
        self.invalidated_at = None
        if self.quality == "disabled":
            self.quality = "candidate"


class QueryExperienceAsset(MetaBase):
    """查询经验关联的表或字段元数据快照"""

    __tablename__ = "query_experience_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    experience_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = mapped_column(String(256), nullable=False)
    database_name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(256))
    meta_version: Mapped[int] = mapped_column(Integer, nullable=False)
    experience: Mapped[QueryExperience] = relationship(back_populates="assets")

    __table_args__ = (
        UniqueConstraint(
            "experience_id",
            "resource_key",
            name="uq_query_experience_asset_resource",
        ),
        CheckConstraint(
            "(kind = 'table' AND column_name IS NULL) OR "
            "(kind = 'column' AND column_name IS NOT NULL)",
            name="ck_query_experience_asset_kind",
        ),
        Index(
            "ix_query_experience_asset_lookup",
            "kind",
            "table_name",
            "column_name",
        ),
    )
