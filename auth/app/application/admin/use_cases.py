"""Admin 模块用例"""

from dataclasses import dataclass

from loguru import logger

from app.core.database import DBSessionContextFactory
from app.domain.entities import Permission, Role, User
from app.domain.errors import (
    EmailAlreadyExistsError,
    PermissionAlreadyExistsError,
    PermissionNotFoundError,
    RoleAlreadyExistsError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.domain.ports import (
    PermissionRepository,
    RelationRepository,
    RoleRepository,
    SessionRepository,
    TokenRepository,
    UserRepository,
)

from .schemas import (
    PermissionDetailResult,
    PermissionInfoResult,
    PermissionListResult,
    RoleDetailResult,
    RoleInfoResult,
    RoleListResult,
    UserDetailResult,
    UserInfoResult,
    UserListResult,
)


def _ensure_user_id(user_id: int | None) -> int:
    """确保 user.id 非空，用于数据库已知写入后对 id 的防御性检查"""
    if user_id is None:
        raise RuntimeError("user.id should not be None")
    return user_id


def _ensure_role_id(role_id: int | None) -> int:
    """确保 role.id 非空"""
    if role_id is None:
        raise RuntimeError("role.id should not be None")
    return role_id


def _ensure_permission_id(permission_id: int | None) -> int:
    """确保 permission.id 非空"""
    if permission_id is None:
        raise RuntimeError("permission.id should not be None")
    return permission_id


def _user_to_info(user: User, effective: int = 1) -> UserInfoResult:
    """User 实体 → UserInfoResult DTO"""
    return UserInfoResult(
        id=_ensure_user_id(user.id),
        email=user.email,
        username=user.name,
        yn=user.yn,
        effective=effective,
        created_at=user.created_at,
    )


def _role_to_info(role: Role) -> RoleInfoResult:
    """Role 实体 → RoleInfoResult DTO"""
    return RoleInfoResult(
        id=_ensure_role_id(role.id),
        name=role.name,
        yn=role.yn,
        created_at=role.created_at,
    )


def _permission_to_info(permission: Permission, effective: int = 1) -> PermissionInfoResult:
    """Permission 实体 → PermissionInfoResult DTO"""
    return PermissionInfoResult(
        id=_ensure_permission_id(permission.id),
        name=permission.name,
        description=permission.description,
        yn=permission.yn,
        effective=effective,
        created_at=permission.created_at,
    )


def _merge_role_permissions(roles: list) -> list[PermissionInfoResult]:  # noqa: F821
    """从角色列表中提取权限并去重，根据角色 yn 标记 effective（1 覆盖 0）"""
    perm_dict: dict[int, PermissionInfoResult] = {}
    for role in roles:
        for perm in role.permissions:
            pid = _ensure_permission_id(perm.id)
            if role.yn == 0:
                perm_dict.setdefault(pid, _permission_to_info(perm, 0))
            else:
                perm_dict[pid] = _permission_to_info(perm, 1)
    return list(perm_dict.values())


def _merge_role_users(roles: list) -> list[UserInfoResult]:  # noqa: F821
    """从角色列表中提取用户并去重，根据角色 yn 标记 effective（1 覆盖 0）"""
    user_dict: dict[int, UserInfoResult] = {}
    for role in roles:
        for user in role.users:
            uid = _ensure_user_id(user.id)
            if role.yn == 0:
                user_dict.setdefault(uid, _user_to_info(user, 0))
            else:
                user_dict[uid] = _user_to_info(user, 1)
    return list(user_dict.values())


async def _refresh_user_tokens(
    db,
    user_ids: set[int],
    user_repo: UserRepository,
    token_repo: TokenRepository,
) -> None:
    """根据用户当前生效的角色权限刷新其所有访问令牌的 scope"""
    for user_id in user_ids:
        user = await user_repo.get_by_id_with_role_permission(db, user_id)
        permissions = (
            list(
                {
                    perm.name
                    for role in user.roles
                    if role.yn
                    for perm in role.permissions
                    if perm.yn
                }
            )
            if user and user.roles
            else []
        )
        await token_repo.update_all_by_user(db, user_id, permissions)


class CreateUserUseCase:
    """管理员创建用户"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo

    async def execute(self, email: str, username: str, password: str) -> UserInfoResult:
        """创建用户，邮箱重复时抛出 EmailAlreadyExistsError"""
        async with self._db() as db:
            if await self._users.get_by_email(db, email):
                raise EmailAlreadyExistsError
            user = await self._users.create(db, email, username, password)
            _ensure_user_id(user.id)
            await db.commit()
        logger.info(f"Admin created user: {user.email}")
        return _user_to_info(user)


class UpdateUserUseCase:
    """管理员更新用户信息（含禁用时清除令牌与会话）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo
        self._sessions = session_repo
        self._tokens = token_repo

    async def execute(
        self,
        user_id: int,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
        yn: int | None = None,
    ) -> UserInfoResult:
        """更新用户字段；若禁用 (yn=0) 则清除该用户所有令牌与会话"""
        async with self._db() as db:
            user = await self._users.get_by_id(db, user_id)
            if not user:
                raise UserNotFoundError
            if email and email != user.email and await self._users.get_by_email(db, email):
                raise EmailAlreadyExistsError

            user = await self._users.update(
                db,
                user,
                email=email,
                username=username,
                password=password,
                yn=yn,
            )
            ensured_id = _ensure_user_id(user.id)
            if yn == 0:
                await self._tokens.remove_all_by_user(db, ensured_id)
                await self._sessions.remove_all_by_user(db, ensured_id)
            await db.commit()
        logger.info(f"Admin updated user: user_id={ensured_id}")
        return _user_to_info(user)


class RemoveUserUseCase:
    """管理员删除用户（含清除令牌与会话）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._users = user_repo
        self._sessions = session_repo
        self._tokens = token_repo

    async def execute(self, user_id: int) -> None:
        """删除用户并清除其所有令牌与会话"""
        async with self._db() as db:
            user = await self._users.get_by_id(db, user_id)
            if not user:
                raise UserNotFoundError
            ensured_id = _ensure_user_id(user.id)
            await self._users.remove(db, user_id)
            await self._tokens.remove_all_by_user(db, ensured_id)
            await self._sessions.remove_all_by_user(db, ensured_id)
            await db.commit()
        logger.info(f"Admin removed user: {user.name}-{user.email}")


class ListUsersUseCase:
    """管理员分页/全量查询用户列表"""

    def __init__(self, db_factory: DBSessionContextFactory, user_repo: UserRepository) -> None:
        self._db = db_factory
        self._users = user_repo

    async def execute(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> UserListResult:
        """分页查询用户，all=True 时返回全部"""
        async with self._db() as db:
            users, total = await self._users.ls(db, offset, limit, keyword, all)
        return UserListResult(
            total=total,
            items=[_user_to_info(u) for u in users],
        )


class GetUserUseCase:
    """管理员查看用户详情（含角色和权限）"""

    def __init__(self, db_factory: DBSessionContextFactory, user_repo: UserRepository) -> None:
        self._db = db_factory
        self._users = user_repo

    async def execute(self, user_id: int) -> UserDetailResult:
        """获取用户详情，包含角色列表与权限列表（根据角色状态标记 effective）"""
        async with self._db() as db:
            user = await self._users.get_by_id_with_role_permission(db, user_id)
            if not user:
                raise UserNotFoundError
        ensured_id = _ensure_user_id(user.id)
        roles = [_role_to_info(role) for role in user.roles]
        permissions = _merge_role_permissions(user.roles)
        return UserDetailResult(
            id=ensured_id,
            email=user.email,
            username=user.name,
            yn=user.yn,
            created_at=user.created_at,
            roles=roles,
            permissions=permissions,
        )


class CreateRoleUseCase:
    """管理员创建角色"""

    def __init__(self, db_factory: DBSessionContextFactory, role_repo: RoleRepository) -> None:
        self._db = db_factory
        self._roles = role_repo

    async def execute(self, name: str) -> RoleInfoResult:
        """创建角色，同名时抛出 RoleAlreadyExistsError"""
        async with self._db() as db:
            if await self._roles.get_by_name(db, name):
                raise RoleAlreadyExistsError
            role = await self._roles.create(db, name)
            await db.commit()
        logger.info(f"Admin created role: {role.name}")
        return _role_to_info(role)


class UpdateRoleUseCase:
    """管理员更新角色（启用/禁用时刷新关联用户的令牌 scope）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        role_repo: RoleRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._roles = role_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(
        self,
        role_id: int,
        name: str | None = None,
        yn: int | None = None,
    ) -> RoleInfoResult:
        """更新角色；若 yn 变更则刷新该角色下所有用户的令牌 scope"""
        async with self._db() as db:
            role = await self._roles.get_by_id(db, role_id)
            if not role:
                raise RoleNotFoundError
            if name and name != role.name and await self._roles.get_by_name(db, name):
                raise RoleAlreadyExistsError

            original_yn = role.yn
            role = await self._roles.update(db, role, name=name, yn=yn)
            if yn is not None and yn != original_yn:
                rwu = await self._roles.get_by_id_with_user(db, role_id)
                if rwu and rwu.users:
                    user_ids = {_ensure_user_id(u.id) for u in rwu.users}
                    await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin updated role: role_id={_ensure_role_id(role.id)}")
        return _role_to_info(role)


class RemoveRoleUseCase:
    """管理员删除角色（刷新关联用户的令牌 scope）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        role_repo: RoleRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._roles = role_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, role_id: int) -> None:
        """删除角色并刷新关联用户的令牌 scope"""
        async with self._db() as db:
            role = await self._roles.get_by_id_with_user(db, role_id)
            if not role:
                raise RoleNotFoundError
            user_ids = {_ensure_user_id(u.id) for u in role.users}
            role_name = role.name
            await self._roles.remove(db, role_id)
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin removed role: {role_name}")


class ListRolesUseCase:
    """管理员分页/全量查询角色列表"""

    def __init__(self, db_factory: DBSessionContextFactory, role_repo: RoleRepository) -> None:
        self._db = db_factory
        self._roles = role_repo

    async def execute(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> RoleListResult:
        """分页查询角色，all=True 时返回全部"""
        async with self._db() as db:
            roles, total = await self._roles.ls(db, offset, limit, keyword, all)
        return RoleListResult(
            total=total,
            items=[_role_to_info(r) for r in roles],
        )


class GetRoleUseCase:
    """管理员查看角色详情（含用户和权限列表）"""

    def __init__(self, db_factory: DBSessionContextFactory, role_repo: RoleRepository) -> None:
        self._db = db_factory
        self._roles = role_repo

    async def execute(self, role_id: int) -> RoleDetailResult:
        """获取角色详情，包含关联用户列表与权限列表"""
        async with self._db() as db:
            role = await self._roles.get_by_id_with_user_permission(db, role_id)
            if not role:
                raise RoleNotFoundError
        return RoleDetailResult(
            id=_ensure_role_id(role.id),
            name=role.name,
            yn=role.yn,
            created_at=role.created_at,
            users=[_user_to_info(u) for u in role.users],
            permissions=[_permission_to_info(p) for p in role.permissions],
        )


class CreatePermissionUseCase:
    """管理员创建权限"""

    def __init__(
        self, db_factory: DBSessionContextFactory, permission_repo: PermissionRepository
    ) -> None:
        self._db = db_factory
        self._permissions = permission_repo

    async def execute(self, name: str, description: str | None = None) -> PermissionInfoResult:
        """创建权限，同名时抛出 PermissionAlreadyExistsError"""
        async with self._db() as db:
            if await self._permissions.get_by_name(db, name):
                raise PermissionAlreadyExistsError
            permission = await self._permissions.create(db, name, description)
            await db.commit()
        logger.info(f"Admin created permission: {permission.name}")
        return _permission_to_info(permission)


class UpdatePermissionUseCase:
    """管理员更新权限（名称/状态变更时刷新关联用户的令牌 scope）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        permission_repo: PermissionRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._permissions = permission_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(
        self,
        permission_id: int,
        name: str | None = None,
        description: str | None = None,
        yn: int | None = None,
    ) -> PermissionInfoResult:
        """更新权限；若名称或启用状态变更则刷新关联用户的令牌 scope"""
        async with self._db() as db:
            permission = await self._permissions.get_by_id(db, permission_id)
            if not permission:
                raise PermissionNotFoundError

            original_name = permission.name
            original_yn = permission.yn
            if name and name != permission.name and await self._permissions.get_by_name(db, name):
                raise PermissionAlreadyExistsError

            permission = await self._permissions.update(
                db,
                permission,
                name=name,
                description=description,
                yn=yn,
            )
            if (yn is not None and yn != original_yn) or (
                name is not None and name != original_name
            ):
                pwr = await self._permissions.get_by_id_with_role_user(db, permission_id)
                if pwr and pwr.roles:
                    user_ids = {
                        _ensure_user_id(u.id) for role in pwr.roles if role.yn for u in role.users
                    }
                    await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(
            f"Admin updated permission: permission_id={_ensure_permission_id(permission.id)}"
        )
        return _permission_to_info(permission)


class RemovePermissionUseCase:
    """管理员删除权限（刷新关联用户的令牌 scope）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        permission_repo: PermissionRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._permissions = permission_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, permission_id: int) -> None:
        """删除权限并刷新关联用户的令牌 scope"""
        async with self._db() as db:
            permission = await self._permissions.get_by_id_with_role_user(db, permission_id)
            if not permission:
                raise PermissionNotFoundError
            permission_name = permission.name
            user_ids = {
                _ensure_user_id(u.id) for role in permission.roles if role.yn for u in role.users
            }
            await self._permissions.remove(db, permission_id)
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin removed permission: {permission_name}")


class ListPermissionsUseCase:
    """管理员分页/全量查询权限列表"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        permission_repo: PermissionRepository,
    ) -> None:
        self._db = db_factory
        self._permissions = permission_repo

    async def execute(
        self,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> PermissionListResult:
        """分页查询权限，all=True 时返回全部"""
        async with self._db() as db:
            permissions, total = await self._permissions.ls(db, offset, limit, keyword, all)
        return PermissionListResult(
            total=total,
            items=[_permission_to_info(p) for p in permissions],
        )


class GetPermissionUseCase:
    """管理员查看权限详情（含关联角色和用户）"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        permission_repo: PermissionRepository,
    ) -> None:
        self._db = db_factory
        self._permissions = permission_repo

    async def execute(self, permission_id: int) -> PermissionDetailResult:
        """获取权限详情，包含关联角色列表与用户列表（根据角色状态标记 effective）"""
        async with self._db() as db:
            permission = await self._permissions.get_by_id_with_role_user(db, permission_id)
            if not permission:
                raise PermissionNotFoundError
        roles = [_role_to_info(role) for role in permission.roles]
        users = _merge_role_users(permission.roles)
        return PermissionDetailResult(
            id=_ensure_permission_id(permission.id),
            name=permission.name,
            description=permission.description,
            yn=permission.yn,
            created_at=permission.created_at,
            roles=roles,
            users=users,
        )


class AddUserRoleUseCase:
    """管理员批量添加用户-角色关联"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        relation_repo: RelationRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._relations = relation_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, pairs: list[tuple[int, int]]) -> None:
        """批量添加用户-角色关联并刷新相关用户的令牌 scope"""
        async with self._db() as db:
            await self._relations.add_user_role(db, pairs)
            user_ids = {uid for uid, _ in pairs}
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin batch added user-role relation {pairs}")


class RemoveUserRoleUseCase:
    """管理员批量移除用户-角色关联"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        relation_repo: RelationRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._relations = relation_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, pairs: list[tuple[int, int]]) -> None:
        """批量移除用户-角色关联并刷新相关用户的令牌 scope"""
        async with self._db() as db:
            await self._relations.remove_user_role(db, pairs)
            user_ids = {uid for uid, _ in pairs}
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin batch removed user-role relation {pairs}")


class AddRolePermissionUseCase:
    """管理员批量添加角色-权限关联"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        relation_repo: RelationRepository,
        role_repo: RoleRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._relations = relation_repo
        self._roles = role_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, pairs: list[tuple[int, int]]) -> None:
        """批量添加角色-权限关联并刷新受影响用户的令牌 scope"""
        async with self._db() as db:
            await self._relations.add_role_permission(db, pairs)
            role_ids = {rid for rid, _ in pairs}
            user_ids: set[int] = set()
            for role_id in role_ids:
                role = await self._roles.get_by_id_with_user(db, role_id)
                if role and role.users:
                    user_ids.update(_ensure_user_id(u.id) for u in role.users)
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin batch added role-permission relation {pairs}")


class RemoveRolePermissionUseCase:
    """管理员批量移除角色-权限关联"""

    def __init__(
        self,
        db_factory: DBSessionContextFactory,
        relation_repo: RelationRepository,
        role_repo: RoleRepository,
        user_repo: UserRepository,
        token_repo: TokenRepository,
    ) -> None:
        self._db = db_factory
        self._relations = relation_repo
        self._roles = role_repo
        self._users = user_repo
        self._tokens = token_repo

    async def execute(self, pairs: list[tuple[int, int]]) -> None:
        """批量移除角色-权限关联并刷新受影响用户的令牌 scope"""
        async with self._db() as db:
            await self._relations.remove_role_permission(db, pairs)
            role_ids = {rid for rid, _ in pairs}
            user_ids: set[int] = set()
            for role_id in role_ids:
                role = await self._roles.get_by_id_with_user(db, role_id)
                if role and role.users:
                    user_ids.update(_ensure_user_id(u.id) for u in role.users)
            await _refresh_user_tokens(db, user_ids, self._users, self._tokens)
            await db.commit()
        logger.info(f"Admin batch removed role-permission relation {pairs}")


@dataclass(frozen=True, slots=True)
class AdminUseCases:
    """管理员用例集合 — 组合根注入"""

    create_user: CreateUserUseCase
    update_user: UpdateUserUseCase
    remove_user: RemoveUserUseCase
    list_users: ListUsersUseCase
    get_user: GetUserUseCase
    create_role: CreateRoleUseCase
    update_role: UpdateRoleUseCase
    remove_role: RemoveRoleUseCase
    list_roles: ListRolesUseCase
    get_role: GetRoleUseCase
    create_permission: CreatePermissionUseCase
    update_permission: UpdatePermissionUseCase
    remove_permission: RemovePermissionUseCase
    list_permissions: ListPermissionsUseCase
    get_permission: GetPermissionUseCase
    add_user_role: AddUserRoleUseCase
    remove_user_role: RemoveUserRoleUseCase
    add_role_permission: AddRolePermissionUseCase
    remove_role_permission: RemoveRolePermissionUseCase


def create_admin_use_cases(
    db_factory: DBSessionContextFactory,
    user_repo: UserRepository,
    session_repo: SessionRepository,
    token_repo: TokenRepository,
    role_repo: RoleRepository,
    permission_repo: PermissionRepository,
    relation_repo: RelationRepository,
) -> AdminUseCases:
    """工厂函数：创建所有管理员用例并注入依赖"""
    return AdminUseCases(
        create_user=CreateUserUseCase(db_factory, user_repo),
        update_user=UpdateUserUseCase(db_factory, user_repo, session_repo, token_repo),
        remove_user=RemoveUserUseCase(db_factory, user_repo, session_repo, token_repo),
        list_users=ListUsersUseCase(db_factory, user_repo),
        get_user=GetUserUseCase(db_factory, user_repo),
        create_role=CreateRoleUseCase(db_factory, role_repo),
        update_role=UpdateRoleUseCase(db_factory, role_repo, user_repo, token_repo),
        remove_role=RemoveRoleUseCase(db_factory, role_repo, user_repo, token_repo),
        list_roles=ListRolesUseCase(db_factory, role_repo),
        get_role=GetRoleUseCase(db_factory, role_repo),
        create_permission=CreatePermissionUseCase(db_factory, permission_repo),
        update_permission=UpdatePermissionUseCase(
            db_factory, permission_repo, user_repo, token_repo
        ),
        remove_permission=RemovePermissionUseCase(
            db_factory, permission_repo, user_repo, token_repo
        ),
        list_permissions=ListPermissionsUseCase(db_factory, permission_repo),
        get_permission=GetPermissionUseCase(db_factory, permission_repo),
        add_user_role=AddUserRoleUseCase(db_factory, relation_repo, user_repo, token_repo),
        remove_user_role=RemoveUserRoleUseCase(db_factory, relation_repo, user_repo, token_repo),
        add_role_permission=AddRolePermissionUseCase(
            db_factory, relation_repo, role_repo, user_repo, token_repo
        ),
        remove_role_permission=RemoveRolePermissionUseCase(
            db_factory, relation_repo, role_repo, user_repo, token_repo
        ),
    )
