"""用户仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    Permission,
    Role,
    RoleWithPermissions,
    User,
    UserWithRolePermissions,
    UserWithRoles,
)
from app.domain.ports import PasswordHasher, UserRepository
from app.utils.datetime_str import now_str


def _user_from_row(row) -> User:
    """行记录 → User 实体"""
    return User(**dict(row))


def _role_from_row(row) -> Role:
    """行记录 → Role 实体"""
    return Role(**dict(row))


def _permission_from_row(row) -> Permission:
    """行记录 → Permission 实体"""
    return Permission(**dict(row))


class UserRepo(UserRepository):
    """用户仓库 SQLAlchemy 实现"""

    def __init__(self, password_hasher: PasswordHasher) -> None:
        self._hasher = password_hasher

    async def create(self, db: AsyncSession, email: str, username: str, password: str) -> User:
        """创建用户"""
        now = now_str()
        result = await db.execute(
            text(
                """INSERT INTO `user` (email, name, password_hash, created_at, updated_at)
                   VALUES (:email, :name, :hash, :now, :now)
                   RETURNING id, email, name, password_hash, yn, created_at, updated_at"""
            ),
            {
                "email": email,
                "name": username,
                "hash": self._hasher.hash(password),
                "now": now,
            },
        )
        return _user_from_row(result.mappings().one())

    async def remove(self, db: AsyncSession, user_id: int) -> None:
        """删除用户"""
        await db.execute(text("DELETE FROM `user` WHERE id = :user_id"), {"user_id": user_id})

    async def update(
        self,
        db: AsyncSession,
        user: User,
        *,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
        yn: int | None = None,
    ) -> User:
        """更新用户信息"""
        new_email = user.email if email is None else email
        new_name = user.name if username is None else username
        new_hash = user.password_hash if password is None else self._hasher.hash(password)
        new_yn = user.yn if yn is None else yn
        result = await db.execute(
            text(
                """UPDATE `user`
                   SET email = :email, name = :name, password_hash = :hash,
                       yn = :yn, updated_at = :now
                   WHERE id = :user_id
                   RETURNING id, email, name, password_hash, yn, created_at, updated_at"""
            ),
            {
                "email": new_email,
                "name": new_name,
                "hash": new_hash,
                "yn": new_yn,
                "now": now_str(),
                "user_id": user.id,
            },
        )
        return _user_from_row(result.mappings().one())

    async def get_by_id(self, db: AsyncSession, user_id: int) -> User | None:
        """根据 ID 获取用户"""
        result = await db.execute(
            text(
                "SELECT id, email, name, password_hash, yn, created_at, updated_at "
                "FROM `user` WHERE id = :user_id"
            ),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return _user_from_row(row) if row else None

    async def get_by_id_with_role(self, db: AsyncSession, user_id: int) -> UserWithRoles | None:
        """根据 ID 获取用户及角色"""
        user = await self.get_by_id(db, user_id)
        if user is None:
            return None
        roles = await self._get_roles(db, user_id)
        return UserWithRoles(
            id=user.id,
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            yn=user.yn,
            created_at=user.created_at,
            updated_at=user.updated_at,
            roles=roles,
        )

    async def get_by_id_with_role_permission(
        self, db: AsyncSession, user_id: int
    ) -> UserWithRolePermissions | None:
        """根据 ID 获取用户及角色与权限"""
        user = await self.get_by_id_with_role(db, user_id)
        if user is None:
            return None
        rps: list[RoleWithPermissions] = []
        for role in user.roles:
            perms = await self._get_role_permissions(db, role.id)
            rps.append(
                RoleWithPermissions(
                    id=role.id,
                    name=role.name,
                    yn=role.yn,
                    created_at=role.created_at,
                    updated_at=role.updated_at,
                    permissions=perms,
                )
            )
        return UserWithRolePermissions(
            id=user.id,
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            yn=user.yn,
            created_at=user.created_at,
            updated_at=user.updated_at,
            roles=rps,
        )

    async def get_by_email(self, db: AsyncSession, email: str) -> User | None:
        """根据邮箱获取用户"""
        result = await db.execute(
            text(
                "SELECT id, email, name, password_hash, yn, created_at, updated_at "
                "FROM `user` WHERE email = :email"
            ),
            {"email": email},
        )
        row = result.mappings().first()
        return _user_from_row(row) if row else None

    async def get_by_email_with_role(self, db: AsyncSession, email: str) -> UserWithRoles | None:
        """根据邮箱获取用户及角色"""
        user = await self.get_by_email(db, email)
        if user is None:
            return None
        roles = await self._get_roles(db, user.id)
        return UserWithRoles(
            id=user.id,
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            yn=user.yn,
            created_at=user.created_at,
            updated_at=user.updated_at,
            roles=roles,
        )

    async def get_by_email_with_role_permission(
        self, db: AsyncSession, email: str
    ) -> UserWithRolePermissions | None:
        """根据邮箱获取用户及角色与权限"""
        user = await self.get_by_email_with_role(db, email)
        if user is None:
            return None
        rps: list[RoleWithPermissions] = []
        for role in user.roles:
            perms = await self._get_role_permissions(db, role.id)
            rps.append(
                RoleWithPermissions(
                    id=role.id,
                    name=role.name,
                    yn=role.yn,
                    created_at=role.created_at,
                    updated_at=role.updated_at,
                    permissions=perms,
                )
            )
        return UserWithRolePermissions(
            id=user.id,
            email=user.email,
            name=user.name,
            password_hash=user.password_hash,
            yn=user.yn,
            created_at=user.created_at,
            updated_at=user.updated_at,
            roles=rps,
        )

    async def ls(
        self,
        db: AsyncSession,
        offset: int,
        limit: int,
        keyword: str | None = None,
        all: bool = False,
    ) -> tuple[list[User], int]:
        """分页查询用户列表"""
        params: dict[str, object] = {}
        where = ""
        if keyword:
            where = "WHERE name LIKE :keyword OR email LIKE :keyword"
            params["keyword"] = f"%{keyword}%"

        if keyword or all:
            result = await db.execute(
                text(
                    f"""SELECT id, email, name, password_hash, yn, created_at, updated_at
                       FROM `user` {where} ORDER BY id DESC"""
                ),
                params,
            )
            users = [_user_from_row(row) for row in result.mappings().all()]
            return users, len(users)

        count_result = await db.execute(text("SELECT COUNT(*) FROM `user`"))
        total = count_result.scalar() or 0
        result = await db.execute(
            text(
                """SELECT id, email, name, password_hash, yn, created_at, updated_at
                   FROM `user` ORDER BY id DESC LIMIT :limit OFFSET :offset"""
            ),
            {"limit": limit, "offset": offset},
        )
        users = [_user_from_row(row) for row in result.mappings().all()]
        return users, total

    async def _get_roles(self, db: AsyncSession, user_id: int | None) -> list[Role]:
        """获取用户关联的角色"""
        if user_id is None:
            return []
        result = await db.execute(
            text(
                """SELECT r.id, r.name, r.yn, r.created_at, r.updated_at
                   FROM `role` r
                   JOIN user_role_rel urr ON urr.role_id = r.id
                   WHERE urr.user_id = :user_id
                   ORDER BY r.id DESC"""
            ),
            {"user_id": user_id},
        )
        return [_role_from_row(row) for row in result.mappings().all()]

    async def _get_role_permissions(
        self, db: AsyncSession, role_id: int | None
    ) -> list[Permission]:
        """获取角色关联的权限"""
        if role_id is None:
            return []
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
