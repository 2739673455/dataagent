from pwdlib import PasswordHash
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cfg
from app.utils.datetime_str import now_str

passwd_hash = PasswordHash.recommended()


async def _make_role_name(db_session: AsyncSession, base_name: str) -> str:
    """生成可用的角色名"""
    role_name = base_name
    suffix = 1

    while True:
        stmt = text("SELECT id FROM `role` WHERE name = :name")
        result = await db_session.execute(stmt, {"name": role_name})
        if result.scalar_one_or_none() is None:
            return role_name
        role_name = f"{base_name}_{suffix}"
        suffix += 1


async def create_admin_user(db_session: AsyncSession) -> None:
    """创建管理员用户（如果不存在）"""
    admin_role = cfg.admin.role
    admin_email = cfg.admin.email
    admin_username = cfg.admin.username
    admin_password = cfg.admin.password
    current_time = now_str()

    async with db_session.begin():
        # 查找 * 权限
        permission_stmt = text("SELECT id FROM `permission` WHERE name = :name")
        permission_result = await db_session.execute(permission_stmt, {"name": "*"})
        all_permission_id = permission_result.scalar_one_or_none()

        # 如果权限存在，则结束
        if all_permission_id is not None:
            return

        # 如果权限不存在，则创建 * 权限，并创建一个新角色(默认为管理员角色，如果存在则添加后缀)，将 * 权限添加到角色
        insert_permission_stmt = text(
            """
            INSERT INTO `permission` (name, description, created_at, updated_at)
            VALUES (:name, :description, :created_at, :updated_at)
            RETURNING id
            """
        )
        insert_permission_result = await db_session.execute(
            insert_permission_stmt,
            {
                "name": "*",
                "description": "全部权限",
                "created_at": current_time,
                "updated_at": current_time,
            },
        )
        all_permission_id = insert_permission_result.scalar_one()

        role_name = await _make_role_name(db_session, admin_role)
        insert_role_stmt = text(
            """
            INSERT INTO `role` (name, created_at, updated_at)
            VALUES (:name, :created_at, :updated_at)
            RETURNING id
            """
        )
        insert_role_result = await db_session.execute(
            insert_role_stmt,
            {
                "name": role_name,
                "created_at": current_time,
                "updated_at": current_time,
            },
        )
        role_id = insert_role_result.scalar_one()

        await db_session.execute(
            text(
                """
                INSERT INTO role_permission_rel (role_id, permission_id)
                VALUES (:role_id, :permission_id)
                """
            ),
            {"role_id": role_id, "permission_id": all_permission_id},
        )

        # 查找是否存在预设的管理员用户
        user_stmt = text("SELECT id FROM `user` WHERE email = :email")
        user_result = await db_session.execute(user_stmt, {"email": admin_email})
        user_id = user_result.scalar_one_or_none()

        # 如果用户不存在，则创建
        if user_id is None:
            insert_user_stmt = text(
                """
                INSERT INTO `user` (email, name, password_hash, created_at, updated_at)
                VALUES (:email, :name, :password_hash, :created_at, :updated_at)
                RETURNING id
                """
            )
            insert_user_result = await db_session.execute(
                insert_user_stmt,
                {
                    "email": admin_email,
                    "name": admin_username,
                    "password_hash": passwd_hash.hash(admin_password),
                    "created_at": current_time,
                    "updated_at": current_time,
                },
            )
            user_id = insert_user_result.scalar_one()

            # 将用户添加到角色中
            await db_session.execute(
                text(
                    """
                    INSERT INTO user_role_rel (role_id, user_id)
                    VALUES (:role_id, :user_id)
                    """
                ),
                {"role_id": role_id, "user_id": user_id},
            )
            return

        # 如果用户存在但不在角色中，则修改用户名和密码为预设值，并添加到角色中
        relation_stmt = text(
            """
            SELECT role_id
            FROM user_role_rel
            WHERE role_id = :role_id AND user_id = :user_id
            """
        )
        relation_result = await db_session.execute(
            relation_stmt,
            {"role_id": role_id, "user_id": user_id},
        )
        relation_exists = relation_result.scalar_one_or_none() is not None

        if not relation_exists:
            await db_session.execute(
                text(
                    """
                    UPDATE `user`
                    SET name = :name, password_hash = :password_hash, updated_at = :updated_at
                    WHERE id = :user_id
                    """
                ),
                {
                    "name": admin_username,
                    "password_hash": passwd_hash.hash(admin_password),
                    "updated_at": current_time,
                    "user_id": user_id,
                },
            )
            await db_session.execute(
                text(
                    """
                    INSERT INTO user_role_rel (role_id, user_id)
                    VALUES (:role_id, :user_id)
                    """
                ),
                {"role_id": role_id, "user_id": user_id},
            )
