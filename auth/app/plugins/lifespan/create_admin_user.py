from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cfg
from app.domain.ports import PasswordHasher
from app.utils.datetime_str import now_str


async def create_admin_user(db_session: AsyncSession, password_hasher: PasswordHasher) -> None:
    """确保存在拥有 * 权限的管理员用户

    逻辑：
    1. 若 * 权限存在且已有用户关联该权限 → 直接返回
    2. 否则确保使用 cfg.admin 配置的用户最终拥有 * 权限
    """
    admin_role = cfg.admin.role
    admin_email = cfg.admin.email
    admin_username = cfg.admin.username
    admin_password = cfg.admin.password
    now = now_str()

    async with db_session.begin():
        # 1. 查找 * 权限
        perm_result = await db_session.execute(
            text("SELECT id FROM `permission` WHERE name = :name"), {"name": "*"}
        )
        perm_id = perm_result.scalar_one_or_none()

        # 2. 若 * 权限存在，检查是否已有用户关联
        if perm_id is not None:
            user_result = await db_session.execute(
                text(
                    """SELECT u.id FROM `user` u
                       JOIN user_role_rel urr ON urr.user_id = u.id
                       JOIN role_permission_rel rpr ON rpr.role_id = urr.role_id
                       WHERE rpr.permission_id = :perm_id
                       LIMIT 1"""
                ),
                {"perm_id": perm_id},
            )
            if user_result.scalar_one_or_none() is not None:
                return

        # 3. * 权限不存在则创建
        if perm_id is None:
            perm_result = await db_session.execute(
                text(
                    """INSERT INTO `permission` (name, description, created_at, updated_at)
                       VALUES (:name, :description, :created_at, :updated_at)
                       RETURNING id"""
                ),
                {
                    "name": "*",
                    "description": "全部权限",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            perm_id = perm_result.scalar_one()

        # 4. 查找拥有 * 权限的角色，不存在则创建
        role_result = await db_session.execute(
            text(
                """SELECT r.id FROM `role` r
                   JOIN role_permission_rel rpr ON rpr.role_id = r.id
                   WHERE rpr.permission_id = :perm_id
                   LIMIT 1"""
            ),
            {"perm_id": perm_id},
        )
        role_id = role_result.scalar_one_or_none()

        if role_id is None:
            role_result = await db_session.execute(
                text(
                    """INSERT INTO `role` (name, created_at, updated_at)
                       VALUES (:name, :created_at, :updated_at)
                       RETURNING id"""
                ),
                {"name": admin_role, "created_at": now, "updated_at": now},
            )
            role_id = role_result.scalar_one()

            await db_session.execute(
                text(
                    """INSERT INTO role_permission_rel (role_id, permission_id)
                       VALUES (:role_id, :perm_id)"""
                ),
                {"role_id": role_id, "perm_id": perm_id},
            )

        # 5. 确保默认管理员用户存在
        user_result = await db_session.execute(
            text("SELECT id FROM `user` WHERE email = :email"),
            {"email": admin_email},
        )
        user_id = user_result.scalar_one_or_none()

        if user_id is None:
            user_result = await db_session.execute(
                text(
                    """INSERT INTO `user` (email, name, password_hash, created_at, updated_at)
                       VALUES (:email, :name, :password_hash, :created_at, :updated_at)
                       RETURNING id"""
                ),
                {
                    "email": admin_email,
                    "name": admin_username,
                    "password_hash": password_hasher.hash(admin_password),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            user_id = user_result.scalar_one()
        else:
            await db_session.execute(
                text(
                    """UPDATE `user`
                       SET name = :name, password_hash = :password_hash, updated_at = :updated_at
                       WHERE id = :user_id"""
                ),
                {
                    "name": admin_username,
                    "password_hash": password_hasher.hash(admin_password),
                    "updated_at": now,
                    "user_id": user_id,
                },
            )

        # 6. 确保用户与角色关联
        rel_result = await db_session.execute(
            text(
                """SELECT 1 FROM user_role_rel
                   WHERE user_id = :uid AND role_id = :rid"""
            ),
            {"uid": user_id, "rid": role_id},
        )
        if rel_result.scalar_one_or_none() is None:
            await db_session.execute(
                text(
                    """INSERT INTO user_role_rel (user_id, role_id)
                       VALUES (:uid, :rid)"""
                ),
                {"uid": user_id, "rid": role_id},
            )
