"""管理模块服务"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import DBSessionContextFactory

from ..shared import errors
from ..shared.session_repo import SessionRepo
from ..shared.token_repo import TokenRepo
from ..shared.user_repo import UserRepo
from . import admin_schema
from .role_repo import RoleRepo
from .relation_repo import RelationRepo
from .permission_repo import PermissionRepo


class AdminService:
    """管理服务"""

    def __init__(
        self,
        db_session_context_factory: DBSessionContextFactory,
        user_repo: UserRepo,
        session_repo: SessionRepo,
        token_repo: TokenRepo,
        role_repo: RoleRepo,
        relation_repo: RelationRepo,
        permission_repo: PermissionRepo,
    ) -> None:
        self.db_session_context_factory = db_session_context_factory
        self.user_repo = user_repo
        self.session_repo = session_repo
        self.token_repo = token_repo
        self.role_repo = role_repo
        self.relation_repo = relation_repo
        self.permission_repo = permission_repo

    @staticmethod
    def _require_user_id(user_id: int | None) -> int:
        """确保用户 ID 已存在"""
        if user_id is None:
            raise RuntimeError("user.id should not be None")
        return user_id

    @staticmethod
    def _require_role_id(role_id: int | None) -> int:
        """确保角色 ID 已存在"""
        if role_id is None:
            raise RuntimeError("role.id should not be None")
        return role_id

    @staticmethod
    def _require_permission_id(permission_id: int | None) -> int:
        """确保权限 ID 已存在"""
        if permission_id is None:
            raise RuntimeError("permission.id should not be None")
        return permission_id

    async def _update_users_tokens(
        self,
        db_session: AsyncSession,
        user_ids: set[int],
    ) -> None:
        """刷新用户的令牌权限"""
        for user_id in user_ids:
            user = await self.user_repo.get_by_id_with_role_permission(db_session, user_id)
            permissions = (
                list(
                    {
                        permission.name
                        for role in user.roles
                        if role.yn
                        for permission in role.permissions
                        if permission.yn
                    }
                )
                if user and user.roles
                else []
            )
            await self.token_repo.update_all_tokens(db_session, user_id, permissions)

    async def create_user(
        self,
        email: str,
        username: str,
        password: str,
    ) -> admin_schema.UserInfo:
        """创建用户"""
        async with self.db_session_context_factory() as db_session:
            # 检查邮箱是否已存在
            if await self.user_repo.get_by_email(db_session, email):
                raise errors.EmailAlreadyExistsError
            # 创建用户
            user = await self.user_repo.create(db_session, email, username, password)
            self._require_user_id(user.id)
            await db_session.commit()

        logger.info(f"Admin created user: {user.email}")
        return admin_schema.UserInfo.from_user(user)

    async def update_user(
        self,
        user_id: int,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
        yn: int | None = None,
    ) -> admin_schema.UserInfo:
        """更新用户信息"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户
            user = await self.user_repo.get_by_id(db_session, user_id)
            if not user:
                raise errors.UserNotFoundError
            # 检查邮箱是否与其他用户冲突
            if (
                email
                and email != user.email
                and await self.user_repo.get_by_email(db_session, email)
            ):
                raise errors.EmailAlreadyExistsError

            # 更新用户信息
            user = await self.user_repo.update(
                db_session,
                user,
                email=email,
                username=username,
                password=password,
                yn=yn,
            )
            ensured_user_id = self._require_user_id(user.id)
            # 禁用用户时，撤销其所有会话和访问令牌
            if yn == 0:
                await self.token_repo.remove_all_tokens_by_user(
                    db_session, ensured_user_id
                )
                await self.session_repo.remove_all_sessions(db_session, ensured_user_id)
            await db_session.commit()

        logger.info(f"Admin updated user: user_id={ensured_user_id}")
        return admin_schema.UserInfo.from_user(user)

    async def remove_user(self, user_id: int) -> None:
        """删除用户"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户
            user = await self.user_repo.get_by_id(db_session, user_id)
            if not user:
                raise errors.UserNotFoundError

            ensured_user_id = self._require_user_id(user.id)
            username = user.name
            email = user.email
            # 删除用户，并清理其所有会话和访问令牌
            await self.user_repo.remove(db_session, user_id)
            await self.token_repo.remove_all_tokens_by_user(db_session, ensured_user_id)
            await self.session_repo.remove_all_sessions(db_session, ensured_user_id)
            await db_session.commit()

        logger.info(f"Admin removed user: {username}-{email}")

    async def list_users(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> admin_schema.UserListResponse:
        """分页查询用户列表"""
        async with self.db_session_context_factory() as db_session:
            # 分页查询用户列表
            users, total = await self.user_repo.ls(
                db_session, offset, limit, keyword, all
            )
        return admin_schema.UserListResponse(
            total=total,
            items=[admin_schema.UserInfo.from_user(user) for user in users],
        )

    async def get_user(self, user_id: int) -> admin_schema.UserDetailResponse:
        """获取用户详情"""
        async with self.db_session_context_factory() as db_session:
            # 获取用户及其角色、权限信息
            user = await self.user_repo.get_by_id_with_role_permission(db_session, user_id)
            if not user:
                raise errors.UserNotFoundError

        ensured_user_id = self._require_user_id(user.id)
        roles = [admin_schema.RoleInfo.from_role(role) for role in user.roles]
        # 根据角色启用状态合并权限有效性
        permission_dict: dict[int, admin_schema.PermissionInfo] = {}
        for role in user.roles:
            for permission in role.permissions:
                permission_id = self._require_permission_id(permission.id)
                if role.yn == 0:
                    permission_dict.setdefault(
                        permission_id, admin_schema.PermissionInfo.from_permission(permission, 0)
                    )
                else:
                    permission_dict[permission_id] = admin_schema.PermissionInfo.from_permission(permission, 1)

        return admin_schema.UserDetailResponse(
            id=ensured_user_id,
            email=user.email,
            username=user.name,
            yn=user.yn,
            create_at=user.created_at,
            roles=roles,
            permissions=list(permission_dict.values()),
        )

    async def create_role(self, name: str) -> admin_schema.RoleInfo:
        """创建角色"""
        async with self.db_session_context_factory() as db_session:
            # 检查角色名是否已存在
            if await self.role_repo.get_by_name(db_session, name):
                raise errors.RoleAlreadyExistsError
            # 创建角色
            role = await self.role_repo.create(db_session, name)
            await db_session.commit()

        logger.info(f"Admin created role: {role.name}")
        return admin_schema.RoleInfo.from_role(role)

    async def update_role(
        self,
        role_id: int,
        name: str | None = None,
        yn: int | None = None,
    ) -> admin_schema.RoleInfo:
        """更新角色信息"""
        async with self.db_session_context_factory() as db_session:
            # 获取角色
            role = await self.role_repo.get_by_id(db_session, role_id)
            if not role:
                raise errors.RoleNotFoundError
            # 检查角色名是否与其他角色冲突
            if (
                name
                and name != role.name
                and await self.role_repo.get_by_name(db_session, name)
            ):
                raise errors.RoleAlreadyExistsError

            original_yn = role.yn
            # 更新角色信息
            role = await self.role_repo.update(db_session, role, name=name, yn=yn)
            # 角色启用状态变化时，刷新角色内用户的权限令牌
            if yn is not None and yn != original_yn:
                role_with_users = await self.role_repo.get_by_id_with_user(
                    db_session, role_id
                )
                if role_with_users and role_with_users.user:
                    user_ids = {
                        self._require_user_id(user.id) for user in role_with_users.user
                    }
                    await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin updated role: role_id={self._require_role_id(role.id)}")
        return admin_schema.RoleInfo.from_role(role)

    async def remove_role(self, role_id: int) -> None:
        """删除角色"""
        async with self.db_session_context_factory() as db_session:
            # 获取角色及角色内用户
            role = await self.role_repo.get_by_id_with_user(db_session, role_id)
            if not role:
                raise errors.RoleNotFoundError

            user_ids = {self._require_user_id(user.id) for user in role.user}
            role_name = role.name
            # 删除角色，并刷新受影响用户的权限令牌
            await self.role_repo.remove(db_session, role_id)
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin removed role: {role_name}")

    async def list_roles(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> admin_schema.RoleListResponse:
        """分页查询角色列表"""
        async with self.db_session_context_factory() as db_session:
            # 分页查询角色列表
            roles, total = await self.role_repo.ls(
                db_session, offset, limit, keyword, all
            )
        return admin_schema.RoleListResponse(
            total=total,
            items=[admin_schema.RoleInfo.from_role(role) for role in roles],
        )

    async def get_role(self, role_id: int) -> admin_schema.RoleDetailResponse:
        """获取角色详情"""
        async with self.db_session_context_factory() as db_session:
            # 获取角色及其用户、权限信息
            role = await self.role_repo.get_by_id_with_user_permission(
                db_session, role_id
            )
            if not role:
                raise errors.RoleNotFoundError

        return admin_schema.RoleDetailResponse(
            id=self._require_role_id(role.id),
            name=role.name,
            yn=role.yn,
            create_at=role.created_at,
            users=[admin_schema.UserInfo.from_user(user) for user in role.user],
            permissions=[admin_schema.PermissionInfo.from_permission(permission) for permission in role.permissions],
        )

    async def create_permission(
        self,
        name: str,
        description: str | None = None,
    ) -> admin_schema.PermissionInfo:
        """创建权限"""
        async with self.db_session_context_factory() as db_session:
            # 检查权限名是否已存在
            if await self.permission_repo.get_by_name(db_session, name):
                raise errors.PermissionAlreadyExistsError
            # 创建权限
            permission = await self.permission_repo.create(db_session, name, description)
            await db_session.commit()

        logger.info(f"Admin created permission: {permission.name}")
        return admin_schema.PermissionInfo.from_permission(permission)

    async def update_permission(
        self,
        permission_id: int,
        name: str | None = None,
        description: str | None = None,
        yn: int | None = None,
    ) -> admin_schema.PermissionInfo:
        """更新权限信息"""
        async with self.db_session_context_factory() as db_session:
            # 获取权限
            permission = await self.permission_repo.get_by_id(db_session, permission_id)
            if not permission:
                raise errors.PermissionNotFoundError

            original_name = permission.name
            original_yn = permission.yn
            # 检查权限名是否与其他权限冲突
            if (
                name
                and name != permission.name
                and await self.permission_repo.get_by_name(db_session, name)
            ):
                raise errors.PermissionAlreadyExistsError

            # 更新权限信息
            permission = await self.permission_repo.update(
                db_session,
                permission,
                name=name,
                description=description,
                yn=yn,
            )
            # 权限名或启用状态变化时，刷新受影响用户的权限令牌
            if (yn is not None and yn != original_yn) or (
                name is not None and name != original_name
            ):
                permission_with_roles = await self.permission_repo.get_by_id_with_role_user(
                    db_session, permission_id
                )
                if permission_with_roles and permission_with_roles.roles:
                    user_ids = {
                        self._require_user_id(user.id)
                        for role in permission_with_roles.roles
                        if role.yn
                        for user in role.user
                    }
                    await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin updated permission: permission_id={self._require_permission_id(permission.id)}")
        return admin_schema.PermissionInfo.from_permission(permission)

    async def remove_permission(self, permission_id: int) -> None:
        """删除权限"""
        async with self.db_session_context_factory() as db_session:
            # 获取权限及其关联的角色、用户
            permission = await self.permission_repo.get_by_id_with_role_user(
                db_session, permission_id
            )
            if not permission:
                raise errors.PermissionNotFoundError

            permission_name = permission.name
            user_ids = {
                self._require_user_id(user.id)
                for role in permission.roles
                if role.yn
                for user in role.user
            }
            # 删除权限，并刷新受影响用户的权限令牌
            await self.permission_repo.remove(db_session, permission_id)
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin removed permission: {permission_name}")

    async def list_permissions(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> admin_schema.PermissionListResponse:
        """分页查询权限列表"""
        async with self.db_session_context_factory() as db_session:
            # 分页查询权限列表
            permissions, total = await self.permission_repo.ls(
                db_session, offset, limit, keyword, all
            )
        return admin_schema.PermissionListResponse(
            total=total,
            items=[admin_schema.PermissionInfo.from_permission(permission) for permission in permissions],
        )

    async def get_permission(self, permission_id: int) -> admin_schema.PermissionDetailResponse:
        """获取权限详情"""
        async with self.db_session_context_factory() as db_session:
            # 获取权限及其关联的角色、用户
            permission = await self.permission_repo.get_by_id_with_role_user(
                db_session, permission_id
            )
            if not permission:
                raise errors.PermissionNotFoundError

        roles = [admin_schema.RoleInfo.from_role(role) for role in permission.roles]
        # 根据角色启用状态合并用户有效性
        user_dict: dict[int, admin_schema.UserInfo] = {}
        for role in permission.roles:
            for user in role.user:
                user_id = self._require_user_id(user.id)
                if role.yn == 0:
                    user_dict.setdefault(
                        user_id, admin_schema.UserInfo.from_user(user, 0)
                    )
                else:
                    user_dict[user_id] = admin_schema.UserInfo.from_user(user, 1)

        return admin_schema.PermissionDetailResponse(
            id=self._require_permission_id(permission.id),
            name=permission.name,
            description=permission.description,
            yn=permission.yn,
            create_at=permission.created_at,
            roles=roles,
            users=list(user_dict.values()),
        )

    async def add_user_role(
        self,
        user_role_tuples: list[tuple[int, int]],
    ) -> None:
        """批量添加用户与角色的关联"""
        async with self.db_session_context_factory() as db_session:
            # 建立用户与角色的关联
            await self.relation_repo.add_user_role(db_session, user_role_tuples)
            # 刷新受影响用户的权限令牌
            user_ids = {user_id for user_id, _ in user_role_tuples}
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin batch added user-role relation {user_role_tuples}")

    async def remove_user_role(
        self,
        user_role_tuples: list[tuple[int, int]],
    ) -> None:
        """批量删除用户与角色的关联"""
        async with self.db_session_context_factory() as db_session:
            # 删除用户与角色的关联
            await self.relation_repo.remove_user_role(db_session, user_role_tuples)
            # 刷新受影响用户的权限令牌
            user_ids = {user_id for user_id, _ in user_role_tuples}
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin batch removed user-role relation {user_role_tuples}")

    async def add_role_permission(
        self,
        role_permission_tuples: list[tuple[int, int]],
    ) -> None:
        """批量添加角色与权限的关联"""
        async with self.db_session_context_factory() as db_session:
            # 建立角色与权限的关联
            await self.relation_repo.add_role_permission(db_session, role_permission_tuples)
            role_ids = {role_id for role_id, _ in role_permission_tuples}
            user_ids: set[int] = set()
            # 收集受影响角色内的用户
            for role_id in role_ids:
                role = await self.role_repo.get_by_id_with_user(db_session, role_id)
                if role and role.user:
                    user_ids.update(
                        self._require_user_id(user.id) for user in role.user
                    )
            # 刷新受影响用户的权限令牌
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin batch added role-permission relation {role_permission_tuples}")

    async def remove_role_permission(
        self,
        role_permission_tuples: list[tuple[int, int]],
    ) -> None:
        """批量删除角色与权限的关联"""
        async with self.db_session_context_factory() as db_session:
            # 删除角色与权限的关联
            await self.relation_repo.remove_role_permission(db_session, role_permission_tuples)
            role_ids = {role_id for role_id, _ in role_permission_tuples}
            user_ids: set[int] = set()
            # 收集受影响角色内的用户
            for role_id in role_ids:
                role = await self.role_repo.get_by_id_with_user(db_session, role_id)
                if role and role.user:
                    user_ids.update(
                        self._require_user_id(user.id) for user in role.user
                    )
            # 刷新受影响用户的权限令牌
            await self._update_users_tokens(db_session, user_ids)
            await db_session.commit()

        logger.info(f"Admin batch removed role-permission relation {role_permission_tuples}")
