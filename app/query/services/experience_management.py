"""查询经验管理用例"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from loguru import logger

from app.query import errors as query_error
from app.query.models.execution import QueryExecution
from app.query.repositories.experience_postgres import (
    QueryExperienceOverview,
    QueryExperiencePGRepo,
)
from app.query.services.experience import QueryExperienceIndexScheduler


@dataclass(frozen=True, slots=True)
class QueryExperienceDeletionResult:
    """已受理的查询经验删除请求。"""

    id: UUID
    deletion_requested_at: datetime


class QueryExperienceManagementService:
    """提供查询经验查看、禁用和删除能力。"""

    def __init__(
        self,
        repo: QueryExperiencePGRepo,
        index_scheduler: QueryExperienceIndexScheduler,
    ) -> None:
        """绑定查询经验存储与索引调度器。"""
        self._repo = repo
        self._index_scheduler = index_scheduler

    async def list_overviews(
        self,
        *,
        limit: int,
        offset: int,
        role_name: str | None,
        status: str | None,
        query: str | None,
    ) -> tuple[list[QueryExperienceOverview], int]:
        """分页读取符合筛选条件的查询经验。"""
        normalized_role = role_name.strip() if role_name else None
        normalized_query = query.strip() if query else None
        async with self._repo.session.begin():
            return await self._repo.list_overviews(
                limit=limit,
                offset=offset,
                role_name=normalized_role or None,
                status=status,
                query=normalized_query or None,
            )

    async def get_overview(self, experience_id: UUID) -> QueryExperienceOverview:
        """读取一条查询经验详情。"""
        async with self._repo.session.begin():
            overview = await self._repo.get_overview(experience_id)
        if overview is None:
            raise query_error.QueryExperienceNotFoundError
        return overview

    async def list_source_executions(
        self,
        experience_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[QueryExecution], int]:
        """分页读取查询经验的来源执行记录。"""
        async with self._repo.session.begin():
            if await self._repo.get(experience_id) is None:
                raise query_error.QueryExperienceNotFoundError
            return await self._repo.list_source_executions(
                experience_id,
                limit=limit,
                offset=offset,
            )

    async def disable_experience(
        self,
        experience_id: UUID,
        *,
        operator_id: int,
    ) -> QueryExperienceOverview:
        """将查询经验标记为管理员禁用。"""
        async with self._repo.session.begin():
            experience, changed = await self._repo.disable_manually(
                experience_id,
                operator_id,
            )
            if experience is None:
                raise query_error.QueryExperienceNotFoundError
            if experience.status == "deleting":
                raise query_error.QueryExperienceStateConflictError(
                    detail="删除中的查询经验不能禁用"
                )
            overview = await self._repo.get_overview(experience_id)
            if overview is None:
                raise query_error.QueryExperienceNotFoundError
            revision = experience.revision
        if changed:
            self._index_scheduler.enqueue(experience_id, revision)
            logger.info(
                "管理员禁用查询经验: "
                f"operator_id={operator_id}, experience_id={experience_id}"
            )
        return overview

    async def request_deletion(
        self,
        experience_id: UUID,
        *,
        operator_id: int,
    ) -> QueryExperienceDeletionResult:
        """提交查询经验删除请求。"""
        async with self._repo.session.begin():
            experience, changed = await self._repo.request_deletion(
                experience_id,
                operator_id,
            )
            if experience is None:
                raise query_error.QueryExperienceNotFoundError
            revision = experience.revision
            requested_at = experience.deletion_requested_at
            if requested_at is None:
                raise RuntimeError("删除中的查询经验缺少请求时间")
        if changed:
            self._index_scheduler.enqueue(experience_id, revision)
            logger.info(
                "管理员删除查询经验: "
                f"operator_id={operator_id}, experience_id={experience_id}"
            )
        return QueryExperienceDeletionResult(
            id=experience_id,
            deletion_requested_at=requested_at,
        )
