"""平台管理员、PostgreSQL 角色权限与后台任务路由。"""

from typing import Annotated, Any

from celery.result import AsyncResult
from fastapi import APIRouter, Body, Path, Query, Response, status
from loguru import logger

from app.dependencies import AdminUserDep
from app.identity.api import schemas
from app.identity.api.dependencies import (
    BusinessPgPermissionServiceDep,
    BusinessPgRoleManagementServiceDep,
    UserDeletionServiceDep,
    UserManagementServiceDep,
)
from app.shared.tasks.celery_app import celery_app
from app.shared.tasks.schemas import TaskStatusResponse

router = APIRouter(tags=["admin"])
task_router = APIRouter(tags=["tasks"])
RolePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$",
        description="PostgreSQL 数据角色名",
    ),
]


@router.get("/business_pg-roles", response_model=list[schemas.BusinessPgRoleResponse])
async def list_business_pg_roles(
    _: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> list[schemas.BusinessPgRoleResponse]:
    """列出可分配角色及 PostgreSQL 实时授权状态。"""
    roles = await service.list_roles()
    return [schemas.BusinessPgRoleResponse.from_status(role) for role in roles]


@router.get(
    "/business_pg-roles/workload-groups",
    response_model=list[str],
)
async def list_business_pg_workload_groups(
    _: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> list[str]:
    """列出创建角色时可使用的 PostgreSQL 查询资源配置。"""
    workload_groups = await service.list_workload_groups()
    return list(workload_groups)


@router.get(
    "/business_pg-roles/existing",
    response_model=list[schemas.BusinessPgExistingRoleResponse],
)
async def list_existing_business_pg_roles(
    _: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> list[schemas.BusinessPgExistingRoleResponse]:
    """只读列出 PostgreSQL 中已存在的角色。"""
    roles = await service.list_existing_roles()
    return [
        schemas.BusinessPgExistingRoleResponse(
            name=role.name,
            managed=role.managed,
            business_pg_users=list(role.business_pg_users),
        )
        for role in roles
    ]


@router.post(
    "/business_pg-roles",
    response_model=schemas.BusinessPgRoleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_business_pg_role(
    body: schemas.CreateBusinessPgRoleRequest,
    current_admin: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> schemas.BusinessPgRoleResponse:
    """创建 PostgreSQL 角色、查询用户和加密凭据。"""
    identity = await service.create_role(
        role_name=body.role,
        description=body.description,
        query_user=body.query_user,
        workload_group=body.workload_group,
    )
    logger.info(
        f"管理员创建 PostgreSQL 角色: operator_id={current_admin.id}, "
        f"role={identity.role_name}, query_user={identity.query_user}, "
        f"workload_group={identity.workload_group}"
    )
    return schemas.BusinessPgRoleResponse.from_entity(identity)


@router.put(
    "/business_pg-roles/{role}/default",
    response_model=schemas.BusinessPgRoleResponse,
)
async def set_default_business_pg_role(
    role: RolePath,
    current_admin: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> schemas.BusinessPgRoleResponse:
    """设置新用户使用的缺省 PostgreSQL 角色。"""
    identity = await service.set_default_role(role)
    logger.info(
        f"管理员设置默认 PostgreSQL 角色: operator_id={current_admin.id}, role={role}"
    )
    return schemas.BusinessPgRoleResponse.from_entity(identity)


@router.delete(
    "/business_pg-roles/default",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_default_business_pg_role(
    current_admin: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> Response:
    """清除新用户使用的缺省 PostgreSQL 角色。"""
    await service.clear_default_role()
    logger.info(f"管理员清除默认 PostgreSQL 角色: operator_id={current_admin.id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/business_pg-roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_business_pg_role(
    role: RolePath,
    current_admin: AdminUserDep,
    service: BusinessPgRoleManagementServiceDep,
) -> Response:
    """删除未使用的 PostgreSQL 角色和查询用户。"""
    await service.delete_role(role)
    logger.info(
        f"管理员删除 PostgreSQL 角色: operator_id={current_admin.id}, role={role}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=schemas.UserListResponse)
async def list_users(
    _: AdminUserDep,
    service: UserManagementServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    query: Annotated[str | None, Query(max_length=128)] = None,
) -> schemas.UserListResponse:
    """分页列出用户、管理员标志与唯一 PostgreSQL 角色。"""
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
    service: UserManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员创建新用户。"""
    user = await service.create_user(
        username=body.username,
        email=body.email,
        password=body.password.get_secret_value(),
        business_pg_role=body.business_pg_role,
        is_admin=body.is_admin,
    )
    logger.info(
        f"管理员创建用户: operator_id={current_admin.id}, user_id={user.id}, "
        f"username={user.username}, business_pg_role={user.business_pg_role_name}, "
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


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int,
    body: schemas.UpdateUserRequest,
    current_admin: AdminUserDep,
    service: UserManagementServiceDep,
) -> schemas.UserResponse:
    """平台管理员修改指定用户信息、角色、权限或密码。"""
    kwargs: dict[str, Any] = {}
    if "username" in body.model_fields_set:
        kwargs["username"] = body.username
    if "email" in body.model_fields_set:
        kwargs["email"] = body.email
    if "password" in body.model_fields_set:
        kwargs["password"] = body.password.get_secret_value() if body.password else None
    if "business_pg_role" in body.model_fields_set:
        kwargs["business_pg_role"] = body.business_pg_role
        kwargs["update_business_pg_role"] = True
    if "is_admin" in body.model_fields_set:
        kwargs["is_admin"] = body.is_admin

    user = await service.update_user(user_id, **kwargs)
    logger.info(
        f"管理员更新用户: operator_id={current_admin.id}, user_id={user_id}, "
        f"updated_fields={sorted(body.model_fields_set)}"
    )
    return schemas.UserResponse.from_user(user)


@router.get(
    "/business_pg-roles/{role}/select-grants",
    response_model=list[schemas.AssetGrantResponse],
)
async def list_select_grants(
    role: RolePath,
    _: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> list[schemas.AssetGrantResponse]:
    """列出角色查询账号的实时有效 SELECT 权限。"""
    grants = await service.list_asset_grants(role)
    return [schemas.AssetGrantResponse.from_entity(grant) for grant in grants]


@router.post(
    "/business_pg-roles/{role}/select-grants",
    response_model=list[schemas.AssetGrantResponse],
    status_code=status.HTTP_201_CREATED,
)
async def grant_select(
    role: RolePath,
    body: schemas.SelectGrantRequest,
    current_admin: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> list[schemas.AssetGrantResponse]:
    """直接向 PostgreSQL 角色授予库、表或列 SELECT 权限。"""
    grants = await service.grant_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    logger.info(
        f"管理员授予 PostgreSQL SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, table={body.table_name}, columns={body.columns}"
    )
    return [schemas.AssetGrantResponse.from_entity(grant) for grant in grants]


@router.delete(
    "/business_pg-roles/{role}/select-grants",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_select(
    role: RolePath,
    body: Annotated[schemas.SelectGrantRequest, Body()],
    current_admin: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> Response:
    """直接从 PostgreSQL 角色回收库、表或列 SELECT 权限。"""
    await service.revoke_select(
        role,
        table_name=body.table_name,
        columns=body.columns,
    )
    logger.info(
        f"管理员回收 PostgreSQL SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, table={body.table_name}, columns={body.columns}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/business_pg-roles/{role}/select-grants/all",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_all_select(
    role: RolePath,
    current_admin: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> Response:
    """回收角色在当前数据库中的全部 SELECT 权限。"""
    revoked_count = await service.revoke_all_select(role)
    logger.info(
        f"管理员清空 PostgreSQL SELECT 权限: operator_id={current_admin.id}, "
        f"role={role}, revoked_count={revoked_count}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/business_pg-roles/{role}/row-policies",
    response_model=list[schemas.RowPolicyResponse],
)
async def list_row_policies(
    role: RolePath,
    _: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> list[schemas.RowPolicyResponse]:
    """直接读取 PostgreSQL 角色的全部行策略。"""
    policies = await service.list_row_policies(role)
    return [schemas.RowPolicyResponse.from_model(policy) for policy in policies]


@router.post(
    "/business_pg-roles/{role}/row-policies",
    status_code=status.HTTP_201_CREATED,
)
async def create_row_policy(
    role: RolePath,
    body: schemas.RowPolicyRequest,
    current_admin: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> Response:
    """直接为 PostgreSQL 角色创建行级过滤策略。"""
    await service.create_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
        policy_type=body.policy_type,
        predicate=body.predicate,
    )
    logger.info(
        f"管理员创建 PostgreSQL 行级策略: operator_id={current_admin.id}, "
        f"role={role}, policy={body.policy_name}, table={body.table_name}, "
        f"policy_type={body.policy_type}"
    )
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/business_pg-roles/{role}/row-policies",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def drop_row_policy(
    role: RolePath,
    body: Annotated[schemas.DropRowPolicyRequest, Body()],
    current_admin: AdminUserDep,
    service: BusinessPgPermissionServiceDep,
) -> Response:
    """直接删除 PostgreSQL 角色的行级过滤策略。"""
    await service.drop_row_policy(
        role,
        policy_name=body.policy_name,
        table_name=body.table_name,
    )
    logger.info(
        f"管理员删除 PostgreSQL 行级策略: operator_id={current_admin.id}, "
        f"role={role}, policy={body.policy_name}, table={body.table_name}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@task_router.get("/{task_id}")
async def get_task_status(
    task_id: str,
    _: AdminUserDep,
) -> TaskStatusResponse:
    """查询后台任务状态和结果。"""
    task = AsyncResult(task_id, app=celery_app)
    result = task.result if task.successful() else None
    error = str(task.result) if task.failed() else None
    return TaskStatusResponse(
        task_id=task_id,
        state=task.state,
        ready=task.ready(),
        successful=task.successful() if task.ready() else None,
        result=result,
        error=error,
    )
