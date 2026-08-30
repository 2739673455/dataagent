"""查询经验管理路由"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi import status as http_status

from app.identity.api.auth.dependencies import AdminUserDep
from app.query.api.admin import schemas
from app.query.api.admin.dependencies import QueryExperienceManagementServiceDep

router = APIRouter(tags=["query-experience-admin"])


@router.get("", response_model=schemas.QueryExperienceListResponse)
async def list_query_experiences(
    _: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    role_name: Annotated[str | None, Query(max_length=256)] = None,
    status: Annotated[
        Literal["active", "disabled", "deleting"] | None,
        Query(),
    ] = None,
    query: Annotated[str | None, Query(max_length=512)] = None,
) -> schemas.QueryExperienceListResponse:
    """分页列出查询经验。"""
    overviews, total = await service.list_overviews(
        limit=limit,
        offset=offset,
        role_name=role_name,
        status=status,
        query=query,
    )
    return schemas.QueryExperienceListResponse(
        items=[
            schemas.QueryExperienceOverviewResponse.from_overview(overview)
            for overview in overviews
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(overviews) < total,
    )


@router.post("/batch-disable", status_code=http_status.HTTP_204_NO_CONTENT)
async def disable_query_experiences(
    body: schemas.QueryExperienceBatchRequest,
    current_admin: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
) -> None:
    """管理员批量禁用查询经验。"""
    await service.disable_experiences(
        body.experience_ids,
        operator_id=current_admin.id,
    )


@router.post("/batch-delete", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_query_experiences(
    body: schemas.QueryExperienceBatchRequest,
    current_admin: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
) -> None:
    """管理员批量提交查询经验删除请求。"""
    await service.request_deletions(
        body.experience_ids,
        operator_id=current_admin.id,
    )


@router.get(
    "/{experience_id}",
    response_model=schemas.QueryExperienceDetailResponse,
)
async def get_query_experience(
    experience_id: UUID,
    _: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
) -> schemas.QueryExperienceDetailResponse:
    """读取查询经验详情。"""
    return schemas.QueryExperienceDetailResponse.from_overview(
        await service.get_overview(experience_id)
    )


@router.get(
    "/{experience_id}/executions",
    response_model=schemas.QueryExperienceSourceExecutionListResponse,
)
async def list_query_experience_source_executions(
    experience_id: UUID,
    _: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> schemas.QueryExperienceSourceExecutionListResponse:
    """分页列出查询经验的来源执行记录。"""
    executions, total = await service.list_source_executions(
        experience_id,
        limit=limit,
        offset=offset,
    )
    return schemas.QueryExperienceSourceExecutionListResponse(
        items=[
            schemas.QueryExperienceSourceExecutionResponse.from_entity(execution)
            for execution in executions
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(executions) < total,
    )


@router.post(
    "/{experience_id}/disable",
    response_model=schemas.QueryExperienceDetailResponse,
)
async def disable_query_experience(
    experience_id: UUID,
    current_admin: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
) -> schemas.QueryExperienceDetailResponse:
    """管理员禁用查询经验。"""
    return schemas.QueryExperienceDetailResponse.from_overview(
        await service.disable_experience(experience_id, operator_id=current_admin.id)
    )


@router.delete(
    "/{experience_id}",
    response_model=schemas.QueryExperienceDeletionResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def delete_query_experience(
    experience_id: UUID,
    current_admin: AdminUserDep,
    service: QueryExperienceManagementServiceDep,
) -> schemas.QueryExperienceDeletionResponse:
    """管理员提交查询经验删除请求。"""
    deletion = await service.request_deletion(
        experience_id,
        operator_id=current_admin.id,
    )
    return schemas.QueryExperienceDeletionResponse.from_deletion_result(deletion)
