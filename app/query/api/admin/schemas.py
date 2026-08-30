"""查询经验管理接口协议"""

from datetime import datetime
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.query.models.execution import QueryExecution
from app.query.models.experience import QUERY_EXPERIENCE_PURPOSE_LIMIT
from app.query.repositories.experience_postgres import QueryExperienceOverview
from app.query.services.experience_management import QueryExperienceDeletionResult

type QueryExperienceStatus = Literal["active", "disabled", "deleting"]
type QueryExperienceDisabledReason = Literal["metadata_changed", "admin"]


class QueryExperienceBatchRequest(BaseModel):
    """批量管理查询经验请求。"""

    model_config = ConfigDict(extra="forbid")

    experience_ids: list[UUID] = Field(min_length=1, max_length=100)


class QueryExperienceOverviewResponse(BaseModel):
    """查询经验管理列表项。"""

    id: UUID
    role_name: str
    status: QueryExperienceStatus
    disabled_reason: QueryExperienceDisabledReason | None
    latest_purpose: str
    purpose_count: int
    sql_template_preview: str
    asset_count: int
    execution_count: int
    last_executed_at: datetime | None
    index_status: Literal["synced", "pending"]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_overview(cls, overview: QueryExperienceOverview) -> Self:
        """从查询经验及聚合统计构造列表项。"""
        experience = overview.experience
        return cls(
            id=experience.id,
            role_name=experience.role_name,
            status=cast(QueryExperienceStatus, experience.status),
            disabled_reason=cast(
                QueryExperienceDisabledReason | None,
                experience.disabled_reason,
            ),
            latest_purpose=experience.purposes[-1] if experience.purposes else "",
            purpose_count=len(experience.purposes),
            sql_template_preview=experience.sql_template[:240],
            asset_count=overview.asset_count,
            execution_count=overview.execution_count,
            last_executed_at=overview.last_executed_at,
            index_status=(
                "synced"
                if experience.indexed_revision == experience.revision
                else "pending"
            ),
            created_at=experience.created_at,
            updated_at=experience.updated_at,
        )


class QueryExperienceListResponse(BaseModel):
    """查询经验管理分页列表。"""

    items: list[QueryExperienceOverviewResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class QueryExperienceAssetResponse(BaseModel):
    """查询经验引用的元数据资产。"""

    kind: Literal["table", "column"]
    database: str
    table: str
    column: str | None


class QueryExperienceDetailResponse(QueryExperienceOverviewResponse):
    """查询经验管理详情。"""

    purposes: list[str]
    sql_template: str
    fingerprint: str
    disabled_by_user_id: int | None
    disabled_at: datetime | None
    deletion_requested_by_user_id: int | None
    deletion_requested_at: datetime | None
    assets: list[QueryExperienceAssetResponse]

    @classmethod
    def from_overview(cls, overview: QueryExperienceOverview) -> Self:
        """从查询经验及聚合统计构造详情。"""
        summary = QueryExperienceOverviewResponse.from_overview(overview)
        experience = overview.experience
        return cls(
            **summary.model_dump(),
            purposes=experience.purposes[-QUERY_EXPERIENCE_PURPOSE_LIMIT:],
            sql_template=experience.sql_template,
            fingerprint=experience.fingerprint,
            disabled_by_user_id=experience.disabled_by_user_id,
            disabled_at=experience.disabled_at,
            deletion_requested_by_user_id=experience.deletion_requested_by_user_id,
            deletion_requested_at=experience.deletion_requested_at,
            assets=[
                QueryExperienceAssetResponse(
                    kind=cast(Literal["table", "column"], asset.kind),
                    database=asset.database_name,
                    table=asset.table_name,
                    column=asset.column_name,
                )
                for asset in sorted(
                    experience.assets,
                    key=lambda item: (
                        item.table_name,
                        item.column_name or "",
                        item.kind,
                    ),
                )
            ],
        )


class QueryExperienceSourceExecutionResponse(BaseModel):
    """查询经验来源执行记录。"""

    id: UUID
    user_id: int
    purpose: str
    analysis_id: str
    session_id: str
    row_count: int | None
    created_at: datetime

    @classmethod
    def from_entity(cls, execution: QueryExecution) -> Self:
        """从查询执行实体构造响应。"""
        row_count = (
            execution.result_summary.get("row_count")
            if execution.result_summary is not None
            else None
        )
        return cls(
            id=execution.id,
            user_id=execution.user_id,
            purpose=execution.purpose,
            analysis_id=execution.analysis_id,
            session_id=execution.session_id,
            row_count=row_count if isinstance(row_count, int) else None,
            created_at=execution.created_at,
        )


class QueryExperienceSourceExecutionListResponse(BaseModel):
    """查询经验来源执行记录分页列表。"""

    items: list[QueryExperienceSourceExecutionResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class QueryExperienceDeletionResponse(BaseModel):
    """查询经验删除请求响应。"""

    id: UUID
    status: Literal["deleting"] = "deleting"
    deletion_requested_at: datetime

    @classmethod
    def from_deletion_result(cls, deletion: QueryExperienceDeletionResult) -> Self:
        """从删除请求结果构造响应。"""
        return cls(
            id=deletion.id,
            deletion_requested_at=deletion.deletion_requested_at,
        )
