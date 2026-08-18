"""平台管理员、用户 Doris 角色与细粒度权限路由"""

from typing import Annotated

from fastapi import APIRouter, Body, Path, Query, Response, status

from app.routes.api.v1.admin import schemas
from app.routes.api.v1.auth.dependencies import (
    AdminUserDep,
    DorisPermissionServiceDep,
    DorisRoleManagementServiceDep,
)

router = APIRouter(tags=["admin"])
RolePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$",
        description="Doris 数据角色名",
    ),
]


@router.get("/doris-roles", response_model=schemas.DorisRoleListResponse)
async def list_doris_roles(
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> schemas.DorisRoleListResponse:
    """列出可分配角色及 Doris 实时授权状态"""
    roles = await service.list_roles()
    return schemas.DorisRoleListResponse(
        roles=[schemas.DorisRoleResponse.from_status(role) for role in roles]
    )


@router.get("/users", response_model=schemas.UserListResponse)
async def list_users(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> schemas.UserListResponse:
    """分页列出用户、管理员标志与唯一 Doris 角色"""
    users = await service.list_users(limit=limit, offset=offset)
    return schemas.UserListResponse(
        users=[schemas.UserResponse.from_entity(user) for user in users]
    )


@router.put("/users/{user_id}/doris-role", response_model=schemas.UserResponse)
async def set_user_doris_role(
    user_id: int,
    body: schemas.SetUserDorisRoleRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """替换指定用户唯一 Doris 数据角色"""
    user = await service.set_user_doris_role(user_id, body.role)
    return schemas.UserResponse.from_entity(user)


@router.put("/users/{user_id}/administrator", response_model=schemas.UserResponse)
async def set_user_administrator(
    user_id: int,
    body: schemas.SetUserAdministratorRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """设置或撤销平台管理员身份"""
    user = await service.set_user_admin(user_id, body.is_admin)
    return schemas.UserResponse.from_entity(user)


@router.get(
    "/doris-roles/{role}/select-grants",
    response_model=schemas.AssetGrantListResponse,
)
async def list_select_grants(
    role: RolePath,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.AssetGrantListResponse:
    """列出角色用于检索前置过滤的 SELECT 权限投影"""
    grants = await service.list_asset_grants(role)
    return schemas.AssetGrantListResponse(
        grants=[schemas.AssetGrantResponse.from_entity(grant) for grant in grants]
    )


@router.post(
    "/doris-roles/{role}/select-grants",
    response_model=schemas.AssetGrantListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_select(
    role: RolePath,
    body: schemas.SelectGrantRequest,
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> schemas.AssetGrantListResponse:
    """直接向 Doris 角色授予库、表或列 SELECT 权限"""
    grants = await service.grant_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    return schemas.AssetGrantListResponse(
        grants=[schemas.AssetGrantResponse.from_entity(grant) for grant in grants]
    )


@router.delete(
    "/doris-roles/{role}/select-grants",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_select(
    role: RolePath,
    body: Annotated[schemas.SelectGrantRequest, Body()],
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接从 Doris 角色回收库、表或列 SELECT 权限"""
    await service.revoke_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/doris-roles/{role}/row-policies",
    response_model=schemas.RowPolicyListResponse,
)
async def list_row_policies(
    role: RolePath,
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> schemas.RowPolicyListResponse:
    """直接读取 Doris 角色的全部行策略"""
    return schemas.RowPolicyListResponse(
        policies=await service.list_row_policies(role)
    )


@router.post(
    "/doris-roles/{role}/row-policies",
    status_code=status.HTTP_201_CREATED,
)
async def create_row_policy(
    role: RolePath,
    body: schemas.RowPolicyRequest,
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接为 Doris 角色创建行级过滤策略"""
    await service.create_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
        policy_type=body.policy_type,
        predicate=body.predicate,
    )
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/doris-roles/{role}/row-policies",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def drop_row_policy(
    role: RolePath,
    body: Annotated[schemas.DropRowPolicyRequest, Body()],
    _: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接删除 Doris 角色的行级过滤策略"""
    await service.drop_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
