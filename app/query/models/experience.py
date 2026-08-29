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
    """按角色和 SQL 结构聚合的共享查询经验"""

    __tablename__ = "query_experiences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    authorization_epoch: Mapped[UUID] = mapped_column(nullable=False, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    sql_template: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )
    disabled_reason: Mapped[str | None] = mapped_column(String(32))
    disabled_by_user_id: Mapped[int | None] = mapped_column(Integer)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_by_user_id: Mapped[int | None] = mapped_column(Integer)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
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
            "role_name",
            "fingerprint",
            name="uq_query_experience_role_fingerprint",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'deleting')",
            name="ck_query_experience_status",
        ),
        CheckConstraint(
            "(status = 'active' AND disabled_reason IS NULL "
            "AND disabled_by_user_id IS NULL AND disabled_at IS NULL "
            "AND deletion_requested_by_user_id IS NULL "
            "AND deletion_requested_at IS NULL) OR "
            "(status = 'disabled' AND disabled_reason IS NOT NULL "
            "AND disabled_at IS NOT NULL "
            "AND deletion_requested_by_user_id IS NULL "
            "AND deletion_requested_at IS NULL) OR "
            "(status = 'deleting' AND disabled_reason IS NULL "
            "AND disabled_by_user_id IS NULL AND disabled_at IS NULL "
            "AND deletion_requested_by_user_id IS NOT NULL "
            "AND deletion_requested_at IS NOT NULL)",
            name="ck_query_experience_status_fields",
        ),
        CheckConstraint(
            "disabled_reason IS NULL OR "
            "(disabled_reason = 'admin' AND disabled_by_user_id IS NOT NULL) OR "
            "(disabled_reason = 'metadata_changed' "
            "AND disabled_by_user_id IS NULL)",
            name="ck_query_experience_disabled_reason",
        ),
        CheckConstraint(
            "revision > 0 AND indexed_revision >= 0",
            name="ck_query_experience_revisions",
        ),
    )

    def refresh_from_success(
        self,
        *,
        purpose: str,
        authorization_epoch: UUID,
        sql_template: str,
    ) -> bool:
        """更新同一角色和 SQL 结构的共享经验。"""
        if self.status == "deleting":
            return False
        if self.authorization_epoch != authorization_epoch:
            self.authorization_epoch = authorization_epoch
            self.purposes = [purpose]
        else:
            self.purposes = [
                *[item for item in self.purposes if item != purpose],
                purpose,
            ][-20:]
        self.sql_template = sql_template
        self.revision += 1
        if self.disabled_reason == "metadata_changed":
            self.status = "active"
            self.disabled_reason = None
            self.disabled_by_user_id = None
            self.disabled_at = None
        return True


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
