"""平台管理员、用户 Doris 角色与细粒度权限路由。"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Response, status
from loguru import logger

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
    """列出可分配角色及 Doris 实时授权状态。"""
    roles = await service.list_roles()
    return schemas.DorisRoleListResponse(
        roles=[schemas.DorisRoleResponse.from_status(role) for role in roles]
    )


@router.get(
    "/doris-roles/workload-groups",
    response_model=schemas.DorisWorkloadGroupListResponse,
)
async def list_doris_workload_groups(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisWorkloadGroupListResponse:
    """列出创建角色时可使用的 Doris 工作组。"""
    workload_groups = await service.list_workload_groups()
    return schemas.DorisWorkloadGroupListResponse(workload_groups=list(workload_groups))


@router.get(
    "/doris-roles/existing",
    response_model=schemas.DorisExistingRoleListResponse,
)
async def list_existing_doris_roles(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisExistingRoleListResponse:
    """只读列出 Doris 中已存在的角色。"""
    roles = await service.list_existing_roles()
    return schemas.DorisExistingRoleListResponse(
        roles=[
            schemas.DorisExistingRoleResponse(
                name=role.name,
                managed=role.managed,
                doris_users=list(role.doris_users),
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
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisRoleResponse:
    """创建 Doris 角色、查询用户和加密凭据。"""
    identity = await service.create_role(
        role_name=body.role,
        description=body.description,
        query_user=body.query_user,
        workload_group=body.workload_group,
    )
    logger.info(
        f"管理员创建 Doris 角色: operator_id={current_admin.id}, "
        f"role={identity.role_name}, query_user={identity.query_user}, "
        f"workload_group={identity.workload_group}"
    )
    return schemas.DorisRoleResponse.from_entity(identity)


@router.put(
    "/doris-roles/{role}/default",
    response_model=schemas.DorisRoleResponse,
)
async def set_default_doris_role(
    role: RolePath,
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.DorisRoleResponse:
    """设置新用户使用的缺省 Doris 角色。"""
    identity = await service.set_default_role(role)
    logger.info(
        f"管理员设置默认 Doris 角色: operator_id={current_admin.id}, role={role}"
    )
    return schemas.DorisRoleResponse.from_entity(identity)


@router.delete(
    "/doris-roles/default",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_default_doris_role(
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> Response:
    """清除新用户使用的缺省 Doris 角色。"""
    await service.clear_default_role()
    logger.info(f"管理员清除默认 Doris 角色: operator_id={current_admin.id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/doris-roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_doris_role(
    role: RolePath,
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> Response:
    """删除未使用的 Doris 角色和查询用户。"""
    await service.delete_role(role)
    logger.info(f"管理员删除 Doris 角色: operator_id={current_admin.id}, role={role}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=schemas.UserListResponse)
async def list_users(
    _: AdminUserDep,
    service: DorisRoleManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    query: Annotated[str | None, Query(max_length=128)] = None,
) -> schemas.UserListResponse:
    """分页列出用户、管理员标志与唯一 Doris 角色。"""
    users, total = await service.list_users(limit=limit, offset=offset, query=query)
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
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员创建新用户。"""
    user = await service.create_user(
        username=body.username,
        email=body.email,
        password=body.password.get_secret_value(),
        doris_role=body.doris_role,
        is_admin=body.is_admin,
    )
    logger.info(
        f"管理员创建用户: operator_id={current_admin.id}, user_id={user.id}, "
        f"username={user.username}, doris_role={user.doris_role_name}, "
        f"is_admin={user.is_admin}"
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
    """平台管理员删除指定用户。"""
    await service.request_deletion(user_id, operator_id=current_admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/users/{user_id}/doris-role", response_model=schemas.UserResponse)
async def set_user_doris_role(
    user_id: int,
    body: schemas.SetUserDorisRoleRequest,
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """替换指定用户唯一 Doris 数据角色。"""
    user = await service.set_user_doris_role(user_id, body.role)
    logger.info(
        f"管理员修改用户 Doris 角色: operator_id={current_admin.id}, "
        f"user_id={user_id}, role={user.doris_role_name}"
    )
    return schemas.UserResponse.from_user(user)


@router.put("/users/{user_id}/administrator", response_model=schemas.UserResponse)
async def set_user_administrator(
    user_id: int,
    body: schemas.SetUserAdministratorRequest,
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """设置或撤销平台管理员身份。"""
    user = await service.set_user_admin(user_id, body.is_admin)
    logger.info(
        f"管理员修改用户管理员权限: operator_id={current_admin.id}, "
        f"user_id={user_id}, is_admin={user.is_admin}"
    )
    return schemas.UserResponse.from_user(user)


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int,
    body: schemas.UpdateUserRequest,
    current_admin: AdminUserDep,
    service: DorisRoleManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员修改指定用户信息、角色、权限或密码。"""
    kwargs: dict[str, Any] = {}
    if "username" in body.model_fields_set:
        kwargs["username"] = body.username
    if "email" in body.model_fields_set:
        kwargs["email"] = body.email
    if "password" in body.model_fields_set:
        kwargs["password"] = body.password.get_secret_value() if body.password else None
    if "doris_role" in body.model_fields_set:
        kwargs["doris_role"] = body.doris_role
        kwargs["update_doris_role"] = True
    if "is_admin" in body.model_fields_set:
        kwargs["is_admin"] = body.is_admin

    user = await service.update_user(user_id, **kwargs)
    logger.info(
        f"管理员更新用户: operator_id={current_admin.id}, user_id={user_id}, "
        f"updated_fields={sorted(body.model_fields_set)}"
    )
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
    """列出角色用于检索前置过滤的 SELECT 权限投影。"""
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
    current_admin: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> schemas.AssetGrantListResponse:
    """直接向 Doris 角色授予库、表或列 SELECT 权限。"""
    grants = await service.grant_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    logger.info(
        f"管理员授予 Doris SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, table={body.table_name}, columns={body.columns}"
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
    current_admin: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接从 Doris 角色回收库、表或列 SELECT 权限。"""
    await service.revoke_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    logger.info(
        f"管理员回收 Doris SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, table={body.table_name}, columns={body.columns}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/doris-roles/{role}/select-grants/all",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_all_select(
    role: RolePath,
    current_admin: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """回收角色在当前数据库中的全部 SELECT 权限。"""
    revoked_count = await service.revoke_all_select(role)
    logger.info(
        f"管理员清空 Doris SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, revoked_count={revoked_count}"
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
    """直接读取 Doris 角色的全部行策略。"""
    policies = await service.list_row_policies(role)
    return schemas.RowPolicyListResponse(
        policies=[schemas.RowPolicyResponse.from_model(policy) for policy in policies]
    )


@router.post(
    "/doris-roles/{role}/row-policies",
    status_code=status.HTTP_201_CREATED,
)
async def create_row_policy(
    role: RolePath,
    body: schemas.RowPolicyRequest,
    current_admin: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接为 Doris 角色创建行级过滤策略。"""
    await service.create_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
        policy_type=body.policy_type,
        predicate=body.predicate,
    )
    logger.info(
        f"管理员创建 Doris 行级策略: operator_id={current_admin.id}, "
        f"role={role}, policy={body.policy_name}, table={body.table_name}, "
        f"policy_type={body.policy_type}"
    )
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/doris-roles/{role}/row-policies",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def drop_row_policy(
    role: RolePath,
    body: Annotated[schemas.DropRowPolicyRequest, Body()],
    current_admin: AdminUserDep,
    service: DorisPermissionServiceDep,
) -> Response:
    """直接删除 Doris 角色的行级过滤策略。"""
    await service.drop_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
    )
    logger.info(
        f"管理员删除 Doris 行级策略: operator_id={current_admin.id}, "
        f"role={role}, policy={body.policy_name}, table={body.table_name}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
