"""关联操作"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RelationRepo:
    """关联数据访问实现"""

    async def add_user_role(
        self, db_session: AsyncSession, user_role_id_tuples: list[tuple[int, int]]
    ) -> None:
        """批量添加用户与角色的关联"""
        for user_id, role_id in set(user_role_id_tuples):
            await db_session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO user_role_rel (user_id, role_id)
                    VALUES (:user_id, :role_id)
                    """
                ),
                {"user_id": user_id, "role_id": role_id},
            )

    async def remove_user_role(
        self, db_session: AsyncSession, user_role_id_tuples: list[tuple[int, int]]
    ) -> None:
        """批量删除用户与角色的关联"""
        for user_id, role_id in set(user_role_id_tuples):
            await db_session.execute(
                text(
                    """
                    DELETE FROM user_role_rel
                    WHERE user_id = :user_id
                      AND role_id = :role_id
                    """
                ),
                {"user_id": user_id, "role_id": role_id},
            )

    async def add_role_permission(
        self, db_session: AsyncSession, role_permission_id_tuples: list[tuple[int, int]]
    ) -> None:
        """批量添加角色与权限的关联"""
        for role_id, permission_id in set(role_permission_id_tuples):
            await db_session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO role_permission_rel (role_id, permission_id)
                    VALUES (:role_id, :permission_id)
                    """
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )

    async def remove_role_permission(
        self, db_session: AsyncSession, role_permission_id_tuples: list[tuple[int, int]]
    ) -> None:
        """批量删除角色与权限的关联"""
        for role_id, permission_id in set(role_permission_id_tuples):
            await db_session.execute(
                text(
                    """
                    DELETE FROM role_permission_rel
                    WHERE role_id = :role_id
                      AND permission_id = :permission_id
                    """
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )


relation_repo = RelationRepo()
