"""用户数据访问"""

from dataclasses import dataclass

from pwdlib._hash import PasswordHash
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.auth import Permission, Role, User
from app.utils.datetime_str import now_str

passwd_hash = PasswordHash.recommended()


def _user_from_row(row) -> User:
    return User(**dict(row))


def _role_from_row(row) -> Role:
    return Role(**dict(row))


def _permission_from_row(row) -> Permission:
    return Permission(**dict(row))


@dataclass
class RoleWithPermissions(Role):
    permissions: list[Permission]


@dataclass
class UserWithRoles(User):
    roles: list[Role]


@dataclass
class UserWithRolePermissions(User):
    roles: list[RoleWithPermissions]


def _user_with_roles(user: User, roles: list[Role]) -> UserWithRoles:
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


def _user_with_role_permissions(
    user: User, roles: list[RoleWithPermissions]
) -> UserWithRolePermissions:
    return UserWithRolePermissions(
        id=user.id,
        email=user.email,
        name=user.name,
        password_hash=user.password_hash,
        yn=user.yn,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=roles,
    )


class UserRepo:
    async def create(
        self,
        db_session: AsyncSession,
        email: str,
        username: str,
        password: str,
    ) -> User:
        """创建用户"""
        current_time = now_str()
        result = await db_session.execute(
            text(
                """
                INSERT INTO `user` (email, name, password_hash, created_at, updated_at)
                VALUES (:email, :name, :password_hash, :created_at, :updated_at)
                RETURNING id, email, name, password_hash, yn, created_at, updated_at
                """
            ),
            {
                "email": email,
                "name": username,
                "password_hash": passwd_hash.hash(password),
                "created_at": current_time,
                "updated_at": current_time,
            },
        )
        return _user_from_row(result.mappings().one())

    async def remove(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> None:
        """删除用户"""
        await db_session.execute(
            text("DELETE FROM `user` WHERE id = :user_id"),
            {"user_id": user_id},
        )

    async def update(
        self,
        db_session: AsyncSession,
        user: User,
        email: str | None = None,
        username: str | None = None,
        password: str | None = None,
        yn: int | None = None,
    ) -> User:
        """更新用户信息"""
        email = user.email if email is None else email
        username = user.name if username is None else username
        password_hash = user.password_hash if password is None else passwd_hash.hash(password)
        yn = user.yn if yn is None else yn
        result = await db_session.execute(
            text(
                """
                UPDATE `user`
                SET email = :email,
                    name = :name,
                    password_hash = :password_hash,
                    yn = :yn,
                    updated_at = :updated_at
                WHERE id = :user_id
                RETURNING id, email, name, password_hash, yn, created_at, updated_at
                """
            ),
            {
                "user_id": user.id,
                "email": email,
                "name": username,
                "password_hash": password_hash,
                "yn": yn,
                "updated_at": now_str(),
            },
        )
        return _user_from_row(result.mappings().one())

    async def get_by_id(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> User | None:
        """根据用户 ID 获取用户"""
        result = await db_session.execute(
            text(
                """
                SELECT id, email, name, password_hash, yn, created_at, updated_at
                FROM `user`
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return _user_from_row(row) if row else None

    async def get_by_id_with_role(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> UserWithRoles | None:
        """根据用户 ID 获取用户，并加载角色"""
        user = await self.get_by_id(db_session, user_id)
        if user is None:
            return None
        return _user_with_roles(user, await self.get_roles(db_session, user_id))

    async def get_by_id_with_role_permission(
        self,
        db_session: AsyncSession,
        user_id: int,
    ) -> UserWithRolePermissions | None:
        """根据用户 ID 获取用户，并加载角色和权限"""
        user = await self.get_by_id_with_role(db_session, user_id)
        if user is None:
            return None
        roles: list[RoleWithPermissions] = []
        for role in user.roles:
            roles.append(
                _role_with_permissions(
                    role,
                    await self.get_role_permissions(db_session, role.id),
                )
            )
        return _user_with_role_permissions(user, roles)

    async def get_by_email(
        self,
        db_session: AsyncSession,
        email: str,
    ) -> User | None:
        """根据邮箱获取用户"""
        result = await db_session.execute(
            text(
                """
                SELECT id, email, name, password_hash, yn, created_at, updated_at
                FROM `user`
                WHERE email = :email
                """
            ),
            {"email": email},
        )
        row = result.mappings().first()
        return _user_from_row(row) if row else None

    async def get_by_email_with_role(
        self,
        db_session: AsyncSession,
        email: str,
    ) -> UserWithRoles | None:
        """根据邮箱获取用户，并加载角色"""
        user = await self.get_by_email(db_session, email)
        if user is None:
            return None
        return _user_with_roles(user, await self.get_roles(db_session, user.id))

    async def get_by_email_with_role_permission(
        self,
        db_session: AsyncSession,
        email: str,
    ) -> UserWithRolePermissions | None:
        """根据邮箱获取用户，并加载角色和权限"""
        user = await self.get_by_email_with_role(db_session, email)
        if user is None:
            return None
        roles: list[RoleWithPermissions] = []
        for role in user.roles:
            roles.append(
                _role_with_permissions(
                    role,
                    await self.get_role_permissions(db_session, role.id),
                )
            )
        return _user_with_role_permissions(user, roles)

    async def ls(
        self,
        db_session: AsyncSession,
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
            result = await db_session.execute(
                text(
                    f"""
                    SELECT id, email, name, password_hash, yn, created_at, updated_at
                    FROM `user`
                    {where}
                    ORDER BY id DESC
                    """
                ),
                params,
            )
            users = [_user_from_row(row) for row in result.mappings().all()]
            return users, len(users)

        count_result = await db_session.execute(text("SELECT COUNT(*) FROM `user`"))
        total = count_result.scalar() or 0

        result = await db_session.execute(
            text(
                """
                SELECT id, email, name, password_hash, yn, created_at, updated_at
                FROM `user`
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        users = [_user_from_row(row) for row in result.mappings().all()]
        return users, total

    async def get_roles(self, db_session: AsyncSession, user_id: int | None) -> list[Role]:
        """获取用户角色"""
        if user_id is None:
            return []
        result = await db_session.execute(
            text(
                """
                SELECT r.id, r.name, r.yn, r.created_at, r.updated_at
                FROM `role` r
                JOIN user_role_rel urr ON urr.role_id = r.id
                WHERE urr.user_id = :user_id
                ORDER BY r.id DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_role_from_row(row) for row in result.mappings().all()]

    async def get_role_permissions(
        self, db_session: AsyncSession, role_id: int | None
    ) -> list[Permission]:
        """获取角色权限"""
        if role_id is None:
            return []
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


user_repo = UserRepo()
