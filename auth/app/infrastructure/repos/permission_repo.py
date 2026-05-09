"""权限仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    Permission,
    PermissionWithRoleUsers,
    Role,
    RoleWithUsers,
    User,
)
from app.domain.ports import PermissionRepository
from app.utils.datetime_str import now_str


def _permission_from_row(row) -> Permission:
    """行记录 → Permission 实体"""
    return Permission(**dict(row))


def _role_from_row(row) -> Role:
    """行记录 → Role 实体"""
    return Role(**dict(row))


def _user_from_row(row) -> User:
    """行记录 → User 实体"""
    return User(**dict(row))


class PermissionRepo(PermissionRepository):
    """权限仓库 SQLAlchemy 实现"""

    async def create(
        self, db: AsyncSession, name: str, description: str | None = None
    ) -> Permission:
        """创建权限"""
        now = now_str()
        result = await db.execute(
            text(
                """INSERT INTO `permission` (name, description, created_at, updated_at)
                   VALUES (:name, :description, :now, :now)
                   RETURNING id, name, description, yn, created_at, updated_at"""
            ),
            {"name": name, "description": description, "now": now},
        )
        return _permission_from_row(result.mappings().one())

    async def remove(self, db: AsyncSession, permission_id: int) -> None:
        """删除权限"""
        await db.execute(
            text("DELETE FROM `permission` WHERE id = :permission_id"),
            {"permission_id": permission_id},
        )

    async def update(
        self,
        db: AsyncSession,
        permission: Permission,
        *,
        name: str | None = None,
        description: str | None = None,
        yn: int | None = None,
    ) -> Permission:
        """更新权限信息"""
        result = await db.execute(
            text(
                """UPDATE `permission`
                   SET name = :name, description = :description, yn = :yn, updated_at = :now
                   WHERE id = :permission_id
                   RETURNING id, name, description, yn, created_at, updated_at"""
            ),
            {
                "permission_id": permission.id,
                "name": permission.name if name is None else name,
                "description": permission.description if description is None else description,
                "yn": permission.yn if yn is None else yn,
                "now": now_str(),
            },
        )
        return _permission_from_row(result.mappings().one())

    async def get_by_id(self, db: AsyncSession, permission_id: int) -> Permission | None:
        """根据 ID 获取权限"""
        result = await db.execute(
            text(
                "SELECT id, name, description, yn, created_at, updated_at "
                "FROM `permission` WHERE id = :permission_id"
            ),
            {"permission_id": permission_id},
        )
        row = result.mappings().first()
        return _permission_from_row(row) if row else None

    async def get_by_name(self, db: AsyncSession, name: str) -> Permission | None:
        """根据名称获取权限"""
        result = await db.execute(
            text(
                "SELECT id, name, description, yn, created_at, updated_at "
                "FROM `permission` WHERE name = :name"
            ),
            {"name": name},
        )
        row = result.mappings().first()
        return _permission_from_row(row) if row else None

    async def get_by_id_with_role_user(
        self, db: AsyncSession, permission_id: int
    ) -> PermissionWithRoleUsers | None:
        """根据 ID 获取权限及关联角色与用户"""
        permission = await self.get_by_id(db, permission_id)
        if permission is None:
            return None
        roles = await self._get_roles(db, permission_id)
        roles_with_users: list[RoleWithUsers] = []
        for role in roles:
            users = await self._get_role_users(db, role.id)
            roles_with_users.append(
                RoleWithUsers(
                    id=role.id,
                    name=role.name,
                    yn=role.yn,
                    created_at=role.created_at,
                    updated_at=role.updated_at,
                    users=users,
                )
            )
        return PermissionWithRoleUsers(
            id=permission.id,
            name=permission.name,
            description=permission.description,
            yn=permission.yn,
            created_at=permission.created_at,
            updated_at=permission.updated_at,
            roles=roles_with_users,
        )

    async def ls(
        self,
        db: AsyncSession,
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
            result = await db.execute(
                text(
                    f"""SELECT id, name, description, yn, created_at, updated_at
                       FROM `permission` {where} ORDER BY id DESC"""
                ),
                params,
            )
            permissions = [_permission_from_row(row) for row in result.mappings().all()]
            return permissions, len(permissions)

        count_result = await db.execute(text("SELECT COUNT(*) FROM `permission`"))
        total = count_result.scalar() or 0
        result = await db.execute(
            text(
                """SELECT id, name, description, yn, created_at, updated_at
                   FROM `permission` ORDER BY id DESC LIMIT :limit OFFSET :offset"""
            ),
            {"limit": limit, "offset": offset},
        )
        permissions = [_permission_from_row(row) for row in result.mappings().all()]
        return permissions, total

    async def _get_roles(self, db: AsyncSession, permission_id: int) -> list[Role]:
        """获取权限关联的角色"""
        result = await db.execute(
            text(
                """SELECT r.id, r.name, r.yn, r.created_at, r.updated_at
                   FROM `role` r
                   JOIN role_permission_rel rpr ON rpr.role_id = r.id
                   WHERE rpr.permission_id = :permission_id
                   ORDER BY r.id DESC"""
            ),
            {"permission_id": permission_id},
        )
        return [_role_from_row(row) for row in result.mappings().all()]

    async def _get_role_users(self, db: AsyncSession, role_id: int | None) -> list[User]:
        """获取角色关联的用户"""
        if role_id is None:
            return []
        result = await db.execute(
            text(
                """SELECT u.id, u.email, u.name, u.password_hash, u.yn, u.created_at, u.updated_at
                   FROM `user` u
                   JOIN user_role_rel urr ON urr.user_id = u.id
                   WHERE urr.role_id = :role_id
                   ORDER BY u.id DESC"""
            ),
            {"role_id": role_id},
        )
        return [_user_from_row(row) for row in result.mappings().all()]
