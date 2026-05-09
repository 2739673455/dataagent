"""Admin HTTP 路由"""

import dataclasses
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel

from app.application.admin.use_cases import AdminUseCases
from app.domain.errors import InsufficientPermissionsError, InvalidAccessTokenError
from app.domain.ports import TokenRepository
from app.presentation.deps import (
    AccessTokenPayload,
    resolve_access_token_from_header,
)

from . import schemas


def _convert[T: BaseModel](dto: Any, pydantic_cls: type[T]) -> T:
    """dataclass DTO → Pydantic 模型转换"""
    return pydantic_cls.model_validate(dataclasses.asdict(dto))


def create_router(
    admin_use_cases: AdminUseCases,
    token_repo: TokenRepository,
) -> APIRouter:
    async def require_admin_permission(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AccessTokenPayload:
        """管理员权限依赖：验证访问令牌并检查是否包含通配符 scope"""
        payload = await resolve_access_token_from_header(authorization, token_repo)
        if payload is None:
            raise InvalidAccessTokenError
        if "*" not in payload.scope:
            raise InsufficientPermissionsError(detail="缺少管理员权限")
        return payload

    router = APIRouter(dependencies=[Depends(require_admin_permission)])

    @router.post("/create_user", status_code=status.HTTP_201_CREATED)
    async def create_user(
        body: schemas.CreateUserRequest,
    ) -> schemas.UserInfo:
        """管理员创建用户"""
        result = await admin_use_cases.create_user.execute(body.email, body.username, body.password)
        return _convert(result, schemas.UserInfo)

    @router.post("/update_user")
    async def update_user(
        body: schemas.UpdateUserRequest,
    ) -> schemas.UserInfo:
        """管理员更新用户信息"""
        result = await admin_use_cases.update_user.execute(
            body.user_id,
            email=body.email,
            username=body.username,
            password=body.password,
            yn=body.yn,
        )
        return _convert(result, schemas.UserInfo)

    @router.post("/remove_user")
    async def remove_user(body: schemas.RemoveUserRequest) -> None:
        """管理员删除用户"""
        await admin_use_cases.remove_user.execute(body.user_id)

    @router.get("/list_users")
    async def list_users(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> schemas.UserListResponse:
        """管理员分页查询用户列表"""
        result = await admin_use_cases.list_users.execute(offset, limit, keyword, all)
        return schemas.UserListResponse(
            total=result.total,
            items=[_convert(item, schemas.UserInfo) for item in result.items],
        )

    @router.get("/user/{user_id}")
    async def get_user(user_id: int) -> schemas.UserDetailResponse:
        """管理员查看用户详情（含角色和权限）"""
        result = await admin_use_cases.get_user.execute(user_id)
        return _convert(result, schemas.UserDetailResponse)

    @router.post("/create_role", status_code=status.HTTP_201_CREATED)
    async def create_role(
        body: schemas.CreateRoleRequest,
    ) -> schemas.RoleInfo:
        """管理员创建角色"""
        result = await admin_use_cases.create_role.execute(body.name)
        return _convert(result, schemas.RoleInfo)

    @router.post("/update_role")
    async def update_role(
        body: schemas.UpdateRoleRequest,
    ) -> schemas.RoleInfo:
        """管理员更新角色"""
        result = await admin_use_cases.update_role.execute(
            body.role_id,
            name=body.name,
            yn=body.yn,
        )
        return _convert(result, schemas.RoleInfo)

    @router.post("/remove_role")
    async def remove_role(body: schemas.RemoveRoleRequest) -> None:
        """管理员删除角色"""
        await admin_use_cases.remove_role.execute(body.role_id)

    @router.get("/list_roles")
    async def list_roles(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> schemas.RoleListResponse:
        """管理员分页查询角色列表"""
        result = await admin_use_cases.list_roles.execute(offset, limit, keyword, all)
        return schemas.RoleListResponse(
            total=result.total,
            items=[_convert(item, schemas.RoleInfo) for item in result.items],
        )

    @router.get("/role/{role_id}")
    async def get_role(role_id: int) -> schemas.RoleDetailResponse:
        """管理员查看角色详情（含用户和权限）"""
        result = await admin_use_cases.get_role.execute(role_id)
        return _convert(result, schemas.RoleDetailResponse)

    @router.post("/create_permission", status_code=status.HTTP_201_CREATED)
    async def create_permission(
        body: schemas.CreatePermissionRequest,
    ) -> schemas.PermissionInfo:
        """管理员创建权限"""
        result = await admin_use_cases.create_permission.execute(body.name, body.description)
        return _convert(result, schemas.PermissionInfo)

    @router.post("/update_permission")
    async def update_permission(
        body: schemas.UpdatePermissionRequest,
    ) -> schemas.PermissionInfo:
        """管理员更新权限"""
        result = await admin_use_cases.update_permission.execute(
            body.permission_id,
            name=body.name,
            description=body.description,
            yn=body.yn,
        )
        return _convert(result, schemas.PermissionInfo)

    @router.post("/remove_permission")
    async def remove_permission(body: schemas.RemovePermissionRequest) -> None:
        """管理员删除权限"""
        await admin_use_cases.remove_permission.execute(body.permission_id)

    @router.get("/list_permissions")
    async def list_permissions(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> schemas.PermissionListResponse:
        """管理员分页查询权限列表"""
        result = await admin_use_cases.list_permissions.execute(offset, limit, keyword, all)
        return schemas.PermissionListResponse(
            total=result.total,
            items=[_convert(item, schemas.PermissionInfo) for item in result.items],
        )

    @router.get("/permission/{permission_id}")
    async def get_permission(
        permission_id: int,
    ) -> schemas.PermissionDetailResponse:
        """管理员查看权限详情（含关联角色和用户）"""
        result = await admin_use_cases.get_permission.execute(permission_id)
        return _convert(result, schemas.PermissionDetailResponse)

    @router.post("/user-role/add", status_code=status.HTTP_201_CREATED)
    async def add_user_role(
        body: schemas.BatchAddUserRoleRequest,
    ) -> None:
        """管理员批量添加用户-角色关联"""
        await admin_use_cases.add_user_role.execute(
            [(r.user_id, r.role_id) for r in body.relations]
        )

    @router.post("/user-role/remove")
    async def remove_user_role(
        body: schemas.BatchRemoveUserRoleRequest,
    ) -> None:
        """管理员批量移除用户-角色关联"""
        await admin_use_cases.remove_user_role.execute(
            [(r.user_id, r.role_id) for r in body.relations]
        )

    @router.post("/role-permission/add", status_code=status.HTTP_201_CREATED)
    async def add_role_permission(
        body: schemas.BatchAddRolePermissionRequest,
    ) -> None:
        """管理员批量添加角色-权限关联"""
        await admin_use_cases.add_role_permission.execute(
            [(r.role_id, r.permission_id) for r in body.relations]
        )

    @router.post("/role-permission/remove")
    async def remove_role_permission(
        body: schemas.BatchRemoveRolePermissionRequest,
    ) -> None:
        """管理员批量移除角色-权限关联"""
        await admin_use_cases.remove_role_permission.execute(
            [(r.role_id, r.permission_id) for r in body.relations]
        )

    return router
