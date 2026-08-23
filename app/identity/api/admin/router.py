"""平台管理员、用户 Doris 角色与细粒度权限路由"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Response, status

from app.identity.api.admin import schemas
from app.identity.api.auth.dependencies import (
    AdminUserDep,
    DorisPermissionServiceDep,
    DorisRoleManagementServiceDep,
    UserDeletionServiceDep,
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


@router.get(
    "/doris-roles/discover",
    response_model=schemas.DiscoveredDorisRoleListResponse,
)
async def discover_doris_roles(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DiscoveredDorisRoleListResponse:
    """扫描 Doris 集群原生角色及接入状态"""
    roles = await service.discover_roles()
    return schemas.DiscoveredDorisRoleListResponse(
        roles=[
            schemas.DiscoveredDorisRoleResponse(
                name=role.name,
                is_attached=role.is_attached,
                description=role.description,
                query_user=role.query_user,
                workload_group=role.workload_group,
            )
            for role in roles
        ]
    )


@router.post(
    "/doris-roles",
    response_model=schemas.DorisRoleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_doris_role(
    body: schemas.CreateDorisRoleRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisRoleResponse:
    """创建 Doris 角色、查询用户和加密凭据"""
    identity = await service.create_role(
        role_name=body.role,
        description=body.description,
        query_user=body.query_user,
        workload_group=body.workload_group,
        is_default=body.is_default,
    )
    return schemas.DorisRoleResponse.from_entity(identity)


@router.post(
    "/doris-roles/attach",
    response_model=schemas.DorisRoleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def attach_doris_role(
    body: schemas.AttachDorisRoleRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisRoleResponse:
    """接入已有 Doris 角色并自动配置查询用户"""
    identity = await service.attach_role(
        role_name=body.role,
        description=body.description,
        workload_group=body.workload_group,
        query_user=body.query_user,
        is_default=body.is_default,
    )
    return schemas.DorisRoleResponse.from_entity(identity)


@router.put(
    "/doris-roles/{role}/default",
    response_model=schemas.DorisRoleResponse,
)
async def set_default_doris_role(
    role: RolePath,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisRoleResponse:
    """设置新用户使用的缺省 Doris 角色"""
    identity = await service.set_default_role(role)
    return schemas.DorisRoleResponse.from_entity(identity)


@router.delete(
    "/doris-roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_doris_role(
    role: RolePath,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> Response:
    """删除未使用的非缺省 Doris 角色和查询用户"""
    await service.delete_role(role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=schemas.UserListResponse)
async def list_users(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> schemas.UserListResponse:
    """分页列出用户、管理员标志与唯一 Doris 角色"""
    users, total = await service.list_users(limit=limit, offset=offset)
    return schemas.UserListResponse(
        users=[schemas.UserResponse.from_user(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(users) < total,
    )


@router.post(
    "/users",
    response_model=schemas.UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: schemas.CreateUserRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员创建新用户"""
    user = await service.create_user(
        username=body.username,
        email=body.email,
        password=body.password.get_secret_value(),
        doris_role=body.doris_role,
        is_admin=body.is_admin,
    )
    return schemas.UserResponse.from_user(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: int,
    current_admin: AdminUserDep,
    service: UserDeletionServiceDep,
) -> Response:
    """平台管理员删除指定用户"""
    await service.request_deletion(user_id, operator_id=current_admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/users/{user_id}/doris-role", response_model=schemas.UserResponse)
async def set_user_doris_role(
    user_id: int,
    body: schemas.SetUserDorisRoleRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """替换指定用户唯一 Doris 数据角色"""
    user = await service.set_user_doris_role(user_id, body.role)
    return schemas.UserResponse.from_user(user)


@router.put("/users/{user_id}/administrator", response_model=schemas.UserResponse)
async def set_user_administrator(
    user_id: int,
    body: schemas.SetUserAdministratorRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """设置或撤销平台管理员身份"""
    user = await service.set_user_admin(user_id, body.is_admin)
    return schemas.UserResponse.from_user(user)


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int,
    body: schemas.UpdateUserRequest,
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员修改指定用户信息、角色、权限或密码"""
    kwargs: dict[str, Any] = {}
    if "username" in body.model_fields_set:
        kwargs["username"] = body.username
    if "email" in body.model_fields_set:
        kwargs["email"] = body.email
    if "password" in body.model_fields_set:
        kwargs["password"] = body.password.get_secret_value() if body.password else None
    if "doris_role" in body.model_fields_set:
        kwargs["doris_role"] = body.doris_role
    if "is_admin" in body.model_fields_set:
        kwargs["is_admin"] = body.is_admin

    user = await service.update_user(user_id, **kwargs)
    return schemas.UserResponse.from_user(user)


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
    return schemas.RowPolicyListResponse(policies=await service.list_row_policies(role))


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
