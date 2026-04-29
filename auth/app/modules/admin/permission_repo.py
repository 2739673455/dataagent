"""权限数据访问"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.auth import Permission, Role, User
from app.utils.datetime_str import now_str


@dataclass
class RoleWithUsers(Role):
    user: list[User]


@dataclass
class PermissionWithRoles(Permission):
    roles: list[Role]


@dataclass
class PermissionWithRoleUsers(Permission):
    roles: list[RoleWithUsers]


def _permission_from_row(row) -> Permission:
    return Permission(**dict(row))


def _role_from_row(row) -> Role:
    return Role(**dict(row))


def _user_from_row(row) -> User:
    return User(**dict(row))


def _role_with_users(role: Role, users: list[User]) -> RoleWithUsers:
    return RoleWithUsers(
        id=role.id,
        name=role.name,
        yn=role.yn,
        created_at=role.created_at,
        updated_at=role.updated_at,
        user=users,
    )


def _permission_with_roles(
    permission: Permission, roles: list[Role]
) -> PermissionWithRoles:
    return PermissionWithRoles(
        id=permission.id,
        name=permission.name,
        description=permission.description,
        yn=permission.yn,
        created_at=permission.created_at,
        updated_at=permission.updated_at,
        roles=roles,
    )


def _permission_with_role_users(
    permission: Permission, roles: list[RoleWithUsers]
) -> PermissionWithRoleUsers:
    return PermissionWithRoleUsers(
        id=permission.id,
        name=permission.name,
        description=permission.description,
        yn=permission.yn,
        created_at=permission.created_at,
        updated_at=permission.updated_at,
        roles=roles,
    )


class PermissionRepo:
    """权限数据访问实现"""

    async def create(
        self, db_session: AsyncSession, name: str, description: str | None = None
    ) -> Permission:
        """创建权限"""
        current_time = now_str()
        result = await db_session.execute(
            text(
                """
                INSERT INTO `permission` (name, description, created_at, updated_at)
                VALUES (:name, :description, :created_at, :updated_at)
                RETURNING id, name, description, yn, created_at, updated_at
                """
            ),
            {
                "name": name,
                "description": description,
                "created_at": current_time,
                "updated_at": current_time,
            },
        )
        return _permission_from_row(result.mappings().one())

    async def remove(self, db_session: AsyncSession, permission_id: int) -> None:
        """删除权限"""
        await db_session.execute(
            text("DELETE FROM `permission` WHERE id = :permission_id"),
            {"permission_id": permission_id},
        )

    async def update(
        self,
        db_session: AsyncSession,
        permission: Permission,
        name: str | None = None,
        description: str | None = None,
        yn: int | None = None,
    ) -> Permission:
        """更新权限信息"""
        result = await db_session.execute(
            text(
                """
                UPDATE `permission`
                SET name = :name,
                    description = :description,
                    yn = :yn,
                    updated_at = :updated_at
                WHERE id = :permission_id
                RETURNING id, name, description, yn, created_at, updated_at
                """
            ),
            {
                "permission_id": permission.id,
                "name": permission.name if name is None else name,
                "description": permission.description if description is None else description,
                "yn": permission.yn if yn is None else yn,
                "updated_at": now_str(),
            },
        )
        return _permission_from_row(result.mappings().one())

    async def get_by_id(
        self, db_session: AsyncSession, permission_id: int
    ) -> Permission | None:
        """根据权限 ID 获取权限"""
        result = await db_session.execute(
            text(
                """
                SELECT id, name, description, yn, created_at, updated_at
                FROM `permission`
                WHERE id = :permission_id
                """
            ),
            {"permission_id": permission_id},
        )
        row = result.mappings().first()
        return _permission_from_row(row) if row else None

    async def get_by_id_with_role(
        self, db_session: AsyncSession, permission_id: int
    ) -> PermissionWithRoles | None:
        """根据权限 ID 获取权限，并加载角色"""
        permission = await self.get_by_id(db_session, permission_id)
        if permission is None:
            return None
        return _permission_with_roles(
            permission,
            await self.get_roles(db_session, permission_id),
        )

    async def get_by_id_with_role_user(
        self, db_session: AsyncSession, permission_id: int
    ) -> PermissionWithRoleUsers | None:
        """根据权限 ID 获取权限，并加载角色和用户"""
        permission = await self.get_by_id(db_session, permission_id)
        if permission is None:
            return None
        roles = await self.get_roles(db_session, permission_id)
        roles_with_users = [
            _role_with_users(role, await self.get_role_users(db_session, role.id))
            for role in roles
        ]
        return _permission_with_role_users(permission, roles_with_users)

    async def get_by_name(
        self, db_session: AsyncSession, name: str
    ) -> Permission | None:
        """根据权限名获取权限"""
        result = await db_session.execute(
            text(
                """
                SELECT id, name, description, yn, created_at, updated_at
                FROM `permission`
                WHERE name = :name
                """
            ),
            {"name": name},
        )
        row = result.mappings().first()
        return _permission_from_row(row) if row else None

    async def ls(
        self,
        db_session: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[Permission], int]:
        """分页查询权限列表"""
        params: dict[str, object] = {}
        where = ""
        if keyword:
            where = "WHERE name LIKE :keyword OR description LIKE :keyword"
            params["keyword"] = f"%{keyword}%"

        if keyword or all:
            result = await db_session.execute(
                text(
                    f"""
                    SELECT id, name, description, yn, created_at, updated_at
                    FROM `permission`
                    {where}
                    ORDER BY id DESC
                    """
                ),
                params,
            )
            permissions = [_permission_from_row(row) for row in result.mappings().all()]
            return permissions, len(permissions)

        count_result = await db_session.execute(text("SELECT COUNT(*) FROM `permission`"))
        total = count_result.scalar() or 0
        result = await db_session.execute(
            text(
                """
                SELECT id, name, description, yn, created_at, updated_at
                FROM `permission`
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        permissions = [_permission_from_row(row) for row in result.mappings().all()]
        return permissions, total

    async def get_roles(self, db_session: AsyncSession, permission_id: int) -> list[Role]:
        """获取权限关联角色"""
        result = await db_session.execute(
            text(
                """
                SELECT r.id, r.name, r.yn, r.created_at, r.updated_at
                FROM `role` r
                JOIN role_permission_rel rpr ON rpr.role_id = r.id
                WHERE rpr.permission_id = :permission_id
                ORDER BY r.id DESC
                """
            ),
            {"permission_id": permission_id},
        )
        return [_role_from_row(row) for row in result.mappings().all()]

    async def get_role_users(
        self, db_session: AsyncSession, role_id: int | None
    ) -> list[User]:
        """获取角色关联用户"""
        if role_id is None:
            return []
        result = await db_session.execute(
            text(
                """
                SELECT u.id, u.email, u.name, u.password_hash, u.yn, u.created_at, u.updated_at
                FROM `user` u
                JOIN user_role_rel urr ON urr.user_id = u.id
                WHERE urr.role_id = :role_id
                ORDER BY u.id DESC
                """
            ),
            {"role_id": role_id},
        )
        return [_user_from_row(row) for row in result.mappings().all()]


permission_repo = PermissionRepo()
