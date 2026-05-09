"""角色仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    Permission,
    Role,
    RoleWithUserPermissions,
    RoleWithUsers,
    User,
)
from app.domain.ports import RoleRepository
from app.utils.datetime_str import now_str


def _role_from_row(row) -> Role:
    """行记录 → Role 实体"""
    return Role(**dict(row))


def _user_from_row(row) -> User:
    """行记录 → User 实体"""
    return User(**dict(row))


def _permission_from_row(row) -> Permission:
    """行记录 → Permission 实体"""
    return Permission(**dict(row))


class RoleRepo(RoleRepository):
    """角色仓库 SQLAlchemy 实现"""

    async def create(self, db: AsyncSession, name: str) -> Role:
        """创建角色"""
        now = now_str()
        result = await db.execute(
            text(
                """INSERT INTO `role` (name, created_at, updated_at)
                   VALUES (:name, :now, :now)
                   RETURNING id, name, yn, created_at, updated_at"""
            ),
            {"name": name, "now": now},
        )
        return _role_from_row(result.mappings().one())

    async def remove(self, db: AsyncSession, role_id: int) -> None:
        """删除角色"""
        await db.execute(
            text("DELETE FROM `role` WHERE id = :role_id"),
            {"role_id": role_id},
        )

    async def update(
        self,
        db: AsyncSession,
        role: Role,
        *,
        name: str | None = None,
        yn: int | None = None,
    ) -> Role:
        """更新角色信息"""
        result = await db.execute(
            text(
                """UPDATE `role`
                   SET name = :name, yn = :yn, updated_at = :now
                   WHERE id = :role_id
                   RETURNING id, name, yn, created_at, updated_at"""
            ),
            {
                "role_id": role.id,
                "name": role.name if name is None else name,
                "yn": role.yn if yn is None else yn,
                "now": now_str(),
            },
        )
        return _role_from_row(result.mappings().one())

    async def get_by_id(self, db: AsyncSession, role_id: int) -> Role | None:
        """根据 ID 获取角色"""
        result = await db.execute(
            text("SELECT id, name, yn, created_at, updated_at FROM `role` WHERE id = :role_id"),
            {"role_id": role_id},
        )
        row = result.mappings().first()
        return _role_from_row(row) if row else None

    async def get_by_name(self, db: AsyncSession, name: str) -> Role | None:
        """根据名称获取角色"""
        result = await db.execute(
            text("SELECT id, name, yn, created_at, updated_at FROM `role` WHERE name = :name"),
            {"name": name},
        )
        row = result.mappings().first()
        return _role_from_row(row) if row else None

    async def get_by_id_with_user(self, db: AsyncSession, role_id: int) -> RoleWithUsers | None:
        """根据 ID 获取角色及用户"""
        role = await self.get_by_id(db, role_id)
        if role is None:
            return None
        users = await self._get_users(db, role_id)
        return RoleWithUsers(
            id=role.id,
            name=role.name,
            yn=role.yn,
            created_at=role.created_at,
            updated_at=role.updated_at,
            users=users,
        )

    async def get_by_id_with_user_permission(
        self, db: AsyncSession, role_id: int
    ) -> RoleWithUserPermissions | None:
        """根据 ID 获取角色及用户与权限"""
        role = await self.get_by_id(db, role_id)
        if role is None:
            return None
        return RoleWithUserPermissions(
            id=role.id,
            name=role.name,
            yn=role.yn,
            created_at=role.created_at,
            updated_at=role.updated_at,
            users=await self._get_users(db, role_id),
            permissions=await self._get_permissions(db, role_id),
        )

    async def ls(
        self,
        db: AsyncSession,
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
            result = await db.execute(
                text(
                    f"""SELECT id, name, yn, created_at, updated_at
                       FROM `role` {where} ORDER BY id DESC"""
                ),
                params,
            )
            roles = [_role_from_row(row) for row in result.mappings().all()]
            return roles, len(roles)

        count_result = await db.execute(text("SELECT COUNT(*) FROM `role`"))
        total = count_result.scalar() or 0
        result = await db.execute(
            text(
                """SELECT id, name, yn, created_at, updated_at
                   FROM `role` ORDER BY id DESC LIMIT :limit OFFSET :offset"""
            ),
            {"limit": limit, "offset": offset},
        )
        roles = [_role_from_row(row) for row in result.mappings().all()]
        return roles, total

    async def _get_users(self, db: AsyncSession, role_id: int) -> list[User]:
        """获取角色关联的用户"""
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

    async def _get_permissions(self, db: AsyncSession, role_id: int) -> list[Permission]:
        """获取角色关联的权限"""
        result = await db.execute(
            text(
                """SELECT p.id, p.name, p.description, p.yn, p.created_at, p.updated_at
                   FROM `permission` p
                   JOIN role_permission_rel rpr ON rpr.permission_id = p.id
                   WHERE rpr.role_id = :role_id
                   ORDER BY p.id DESC"""
            ),
            {"role_id": role_id},
        )
        return [_permission_from_row(row) for row in result.mappings().all()]
