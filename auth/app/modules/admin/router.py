from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from ..auth import auth_error, auth_schema
from ..auth.deps import resolve_access_token_from_header
from . import admin_schema
from .admin_service import AdminService


def create_router(admin_service: AdminService) -> APIRouter:
    """创建管理模块路由"""

    async def require_admin_permission(
        authorization: Annotated[str | None, Header()] = None,
    ) -> auth_schema.AccessTokenPayload:
        """校验管理员权限（拥有 * 权限）"""
        payload = await resolve_access_token_from_header(authorization)
        if payload is None:
            raise auth_error.InvalidAccessTokenError
        if "*" not in payload.scope:
            raise auth_error.InsufficientPermissionsError(detail="缺少管理员权限")
        return payload

    router = APIRouter(dependencies=[Depends(require_admin_permission)])

    @router.post("/create_user", status_code=status.HTTP_201_CREATED)
    async def create_user(
        body: admin_schema.CreateUserRequest,
    ) -> admin_schema.UserInfo:
        """创建用户"""
        return await admin_service.create_user(body.email, body.username, body.password)

    @router.post("/update_user")
    async def update_user(
        body: admin_schema.UpdateUserRequest,
    ) -> admin_schema.UserInfo:
        """更新用户信息"""
        return await admin_service.update_user(
            body.user_id,
            email=body.email,
            username=body.username,
            password=body.password,
            yn=body.yn,
        )

    @router.post("/remove_user")
    async def remove_user(body: admin_schema.RemoveUserRequest) -> None:
        """删除用户"""
        await admin_service.remove_user(body.user_id)

    @router.get("/list_users")
    async def list_users(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> admin_schema.UserListResponse:
        """获取用户列表"""
        return await admin_service.list_users(offset, limit, keyword, all)

    @router.get("/user/{user_id}")
    async def get_user(user_id: int) -> admin_schema.UserDetailResponse:
        """获取用户详情"""
        return await admin_service.get_user(user_id)

    @router.post("/create_role", status_code=status.HTTP_201_CREATED)
    async def create_role(
        body: admin_schema.CreateRoleRequest,
    ) -> admin_schema.RoleInfo:
        """创建角色"""
        return await admin_service.create_role(body.name)

    @router.post("/update_role")
    async def update_role(
        body: admin_schema.UpdateRoleRequest,
    ) -> admin_schema.RoleInfo:
        """更新角色信息"""
        return await admin_service.update_role(
            body.role_id,
            name=body.name,
            yn=body.yn,
        )

    @router.post("/remove_role")
    async def remove_role(body: admin_schema.RemoveRoleRequest) -> None:
        """删除角色"""
        await admin_service.remove_role(body.role_id)

    @router.get("/list_roles")
    async def list_roles(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> admin_schema.RoleListResponse:
        """获取角色列表"""
        return await admin_service.list_roles(offset, limit, keyword, all)

    @router.get("/role/{role_id}")
    async def get_role(role_id: int) -> admin_schema.RoleDetailResponse:
        """获取角色详情"""
        return await admin_service.get_role(role_id)

    @router.post("/create_permission", status_code=status.HTTP_201_CREATED)
    async def create_permission(
        body: admin_schema.CreatePermissionRequest,
    ) -> admin_schema.PermissionInfo:
        """创建权限"""
        return await admin_service.create_permission(body.name, body.description)

    @router.post("/update_permission")
    async def update_permission(
        body: admin_schema.UpdatePermissionRequest,
    ) -> admin_schema.PermissionInfo:
        """更新权限信息"""
        return await admin_service.update_permission(
            body.permission_id,
            name=body.name,
            description=body.description,
            yn=body.yn,
        )

    @router.post("/remove_permission")
    async def remove_permission(body: admin_schema.RemovePermissionRequest) -> None:
        """删除权限"""
        await admin_service.remove_permission(body.permission_id)

    @router.get("/list_permissions")
    async def list_permissions(
        offset: int = Query(default=0, ge=0, description="偏移量"),
        limit: int = Query(default=20, ge=1, le=1000, description="每页数量"),
        keyword: str | None = Query(default=None, description="搜索关键字"),
        all: bool = Query(default=False, description="是否查询全部数据"),
    ) -> admin_schema.PermissionListResponse:
        """获取权限列表"""
        return await admin_service.list_permissions(offset, limit, keyword, all)

    @router.get("/permission/{permission_id}")
    async def get_permission(permission_id: int) -> admin_schema.PermissionDetailResponse:
        """获取权限详情"""
        return await admin_service.get_permission(permission_id)

    @router.post("/user-role/add", status_code=status.HTTP_201_CREATED)
    async def add_user_role(
        body: admin_schema.BatchAddUserRoleRequest,
    ) -> None:
        """批量添加用户与角色的关联"""
        await admin_service.add_user_role(
            [(relation.user_id, relation.role_id) for relation in body.relations]
        )

    @router.post("/user-role/remove")
    async def remove_user_role(
        body: admin_schema.BatchRemoveUserRoleRequest,
    ) -> None:
        """批量删除用户与角色的关联"""
        await admin_service.remove_user_role(
            [(relation.user_id, relation.role_id) for relation in body.relations]
        )

    @router.post("/role-permission/add", status_code=status.HTTP_201_CREATED)
    async def add_role_permission(
        body: admin_schema.BatchAddRolePermissionRequest,
    ) -> None:
        """批量添加角色与权限的关联"""
        await admin_service.add_role_permission(
            [(relation.role_id, relation.permission_id) for relation in body.relations]
        )

    @router.post("/role-permission/remove")
    async def remove_role_permission(
        body: admin_schema.BatchRemoveRolePermissionRequest,
    ) -> None:
        """批量删除角色与权限的关联"""
        await admin_service.remove_role_permission(
            [(relation.role_id, relation.permission_id) for relation in body.relations]
        )

    return router
