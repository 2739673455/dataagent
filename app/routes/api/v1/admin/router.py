"""平台角色与数据资产授权管理路由"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from app.entities.auth import PlatformRole
from app.routes.api.v1.admin import schemas
from app.routes.api.v1.auth.dependencies import (
    AdminUserDep,
    RoleManagementServiceDep,
)

router = APIRouter(tags=["admin"])
RolePath = Annotated[PlatformRole, Path(description="平台角色")]


@router.get("/roles", response_model=schemas.RoleListResponse)
async def list_roles(
    _: AdminUserDep,
    service: RoleManagementServiceDep,
) -> schemas.RoleListResponse:
    """列出平台基础角色"""
    roles = await service.list_roles()
    return schemas.RoleListResponse(
        roles=[schemas.RoleResponse.from_entity(role) for role in roles]
    )


@router.get("/users", response_model=schemas.UserListResponse)
async def list_users(
    _: AdminUserDep,
    service: RoleManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> schemas.UserListResponse:
    """分页列出用户与角色"""
    users = await service.list_users(limit=limit, offset=offset)
    return schemas.UserListResponse(
        users=[schemas.UserResponse.from_entity(user) for user in users]
    )


@router.put("/users/{user_id}/roles", response_model=schemas.UserResponse)
async def set_user_roles(
    user_id: int,
    body: schemas.SetUserRolesRequest,
    _: AdminUserDep,
    service: RoleManagementServiceDep,
) -> schemas.UserResponse:
    """整体替换指定用户的平台角色"""
    user = await service.set_user_roles(user_id, body.roles)
    return schemas.UserResponse.from_entity(user)


@router.get(
    "/roles/{role}/asset-grants",
    response_model=schemas.AssetGrantListResponse,
)
async def list_asset_grants(
    role: RolePath,
    _: AdminUserDep,
    service: RoleManagementServiceDep,
) -> schemas.AssetGrantListResponse:
    """列出角色的数据资产白名单"""
    grants = await service.list_asset_grants(role)
    return schemas.AssetGrantListResponse(
        grants=[schemas.AssetGrantResponse.from_entity(grant) for grant in grants]
    )


@router.post(
    "/roles/{role}/asset-grants",
    response_model=schemas.AssetGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset_grant(
    role: RolePath,
    body: schemas.AssetGrantRequest,
    _: AdminUserDep,
    service: RoleManagementServiceDep,
) -> schemas.AssetGrantResponse:
    """新增角色的数据资产白名单"""
    grant = await service.create_asset_grant(role, body.to_identity())
    return schemas.AssetGrantResponse.from_entity(grant)


@router.delete(
    "/roles/{role}/asset-grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_asset_grant(
    role: RolePath,
    grant_id: UUID,
    _: AdminUserDep,
    service: RoleManagementServiceDep,
) -> Response:
    """删除角色的数据资产白名单"""
    await service.delete_asset_grant(role, grant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
