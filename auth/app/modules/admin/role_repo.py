"""角色数据访问"""

from dataclasses import dataclass

from app.entities.auth import Permission, Role, User
from app.utils.datetime_str import now_str
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _role_from_row(row) -> Role:
    return Role(**dict(row))


def _user_from_row(row) -> User:
    return User(**dict(row))


def _permission_from_row(row) -> Permission:
    return Permission(**dict(row))


@dataclass
class RoleWithPermissions(Role):
    permissions: list[Permission]


@dataclass
class RoleWithUsers(Role):
    user: list[User]


@dataclass
class RoleWithUserPermissions(Role):
    user: list[User]
    permissions: list[Permission]


def _role_with_permissions(
    role: Role, permissions: list[Permission]
) -> RoleWithPermissions:
    return RoleWithPermissions(
        id=role.id,
        name=role.name,
        yn=role.yn,
        created_at=role.created_at,
        updated_at=role.updated_at,
        permissions=permissions,
    )


def _role_with_users(role: Role, users: list[User]) -> RoleWithUsers:
    return RoleWithUsers(
        id=role.id,
        name=role.name,
        yn=role.yn,
        created_at=role.created_at,
        updated_at=role.updated_at,
        user=users,
    )


def _role_with_user_permissions(
    role: Role, users: list[User], permissions: list[Permission]
) -> RoleWithUserPermissions:
    return RoleWithUserPermissions(
        id=role.id,
        name=role.name,
        yn=role.yn,
        created_at=role.created_at,
        updated_at=role.updated_at,
        user=users,
        permissions=permissions,
    )


class RoleRepo:
    """角色数据访问实现"""

    async def create(self, db_session: AsyncSession, name: str) -> Role:
        """创建角色"""
        current_time = now_str()
        result = await db_session.execute(
            text(
                """
                INSERT INTO `role` (name, created_at, updated_at)
                VALUES (:name, :created_at, :updated_at)
                RETURNING id, name, yn, created_at, updated_at
                """
            ),
            {"name": name, "created_at": current_time, "updated_at": current_time},
        )
        return _role_from_row(result.mappings().one())

    async def remove(self, db_session: AsyncSession, role_id: int) -> None:
        """删除角色"""
        await db_session.execute(
            text("DELETE FROM `role` WHERE id = :role_id"),
            {"role_id": role_id},
        )

    async def update(
        self,
        db_session: AsyncSession,
        role: Role,
        name: str | None = None,
        yn: int | None = None,
    ) -> Role:
        """更新角色信息"""
        result = await db_session.execute(
            text(
                """
                UPDATE `role`
                SET name = :name,
                    yn = :yn,
                    updated_at = :updated_at
                WHERE id = :role_id
                RETURNING id, name, yn, created_at, updated_at
                """
            ),
            {
                "role_id": role.id,
                "name": role.name if name is None else name,
                "yn": role.yn if yn is None else yn,
                "updated_at": now_str(),
            },
        )
        return _role_from_row(result.mappings().one())

    async def get_by_id(self, db_session: AsyncSession, role_id: int) -> Role | None:
        """根据角色 ID 获取角色"""
        result = await db_session.execute(
            text(
                """
                SELECT id, name, yn, created_at, updated_at
                FROM `role`
                WHERE id = :role_id
                """
            ),
            {"role_id": role_id},
        )
        row = result.mappings().first()
        return _role_from_row(row) if row else None

    async def get_by_id_with_permission(
        self, db_session: AsyncSession, role_id: int
    ) -> RoleWithPermissions | None:
        """根据角色 ID 获取角色，并加载权限"""
        role = await self.get_by_id(db_session, role_id)
        if role is None:
            return None
        return _role_with_permissions(
            role,
            await self.get_permissions(db_session, role_id),
        )

    async def get_by_id_with_user(
        self, db_session: AsyncSession, role_id: int
    ) -> RoleWithUsers | None:
        """根据角色 ID 获取角色，并加载用户"""
        role = await self.get_by_id(db_session, role_id)
        if role is None:
            return None
        return _role_with_users(role, await self.get_users(db_session, role_id))

    async def get_by_id_with_user_permission(
        self, db_session: AsyncSession, role_id: int
    ) -> RoleWithUserPermissions | None:
        """根据角色 ID 获取角色，并加载用户和权限"""
        role = await self.get_by_id(db_session, role_id)
        if role is None:
            return None
        return _role_with_user_permissions(
            role,
            await self.get_users(db_session, role_id),
            await self.get_permissions(db_session, role_id),
        )

    async def get_by_name(self, db_session: AsyncSession, name: str) -> Role | None:
        """根据角色名获取角色"""
        result = await db_session.execute(
            text(
                """
                SELECT id, name, yn, created_at, updated_at
                FROM `role`
                WHERE name = :name
                """
            ),
            {"name": name},
        )
        row = result.mappings().first()
        return _role_from_row(row) if row else None

    async def ls(
        self,
        db_session: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[Role], int]:
        """分页查询角色列表"""
        params: dict[str, object] = {}
        where = ""
        if keyword:
            where = "WHERE name LIKE :keyword"
            params["keyword"] = f"%{keyword}%"

        if keyword or all:
            result = await db_session.execute(
                text(
                    f"""
                    SELECT id, name, yn, created_at, updated_at
                    FROM `role`
                    {where}
                    ORDER BY id DESC
                    """
                ),
                params,
            )
            roles = [_role_from_row(row) for row in result.mappings().all()]
            return roles, len(roles)

        count_result = await db_session.execute(text("SELECT COUNT(*) FROM `role`"))
        total = count_result.scalar() or 0
        result = await db_session.execute(
            text(
                """
                SELECT id, name, yn, created_at, updated_at
                FROM `role`
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        roles = [_role_from_row(row) for row in result.mappings().all()]
        return roles, total

    async def get_users(self, db_session: AsyncSession, role_id: int) -> list[User]:
        """获取角色关联用户"""
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

    async def get_permissions(
        self, db_session: AsyncSession, role_id: int
    ) -> list[Permission]:
        """获取角色关联权限"""
        result = await db_session.execute(
            text(
                """
                SELECT p.id, p.name, p.description, p.yn, p.created_at, p.updated_at
                FROM `permission` p
                JOIN role_permission_rel rpr ON rpr.permission_id = p.id
                WHERE rpr.role_id = :role_id
                ORDER BY p.id DESC
                """
            ),
            {"role_id": role_id},
        )
        return [_permission_from_row(row) for row in result.mappings().all()]


role_repo = RoleRepo()
