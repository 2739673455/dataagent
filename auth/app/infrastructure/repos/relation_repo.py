"""关联仓库 — SQLAlchemy 实现"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ports import RelationRepository


class RelationRepo(RelationRepository):
    """关联关系仓库 SQLAlchemy 实现"""

    async def add_user_role(self, db: AsyncSession, pairs: list[tuple[int, int]]) -> None:
        """批量添加用户-角色关联"""
        for user_id, role_id in set(pairs):
            await db.execute(
                text(
                    """INSERT OR IGNORE INTO user_role_rel (user_id, role_id)
                       VALUES (:user_id, :role_id)"""
                ),
                {"user_id": user_id, "role_id": role_id},
            )

    async def remove_user_role(self, db: AsyncSession, pairs: list[tuple[int, int]]) -> None:
        """批量删除用户-角色关联"""
        for user_id, role_id in set(pairs):
            await db.execute(
                text(
                    """DELETE FROM user_role_rel
                       WHERE user_id = :user_id AND role_id = :role_id"""
                ),
                {"user_id": user_id, "role_id": role_id},
            )

    async def add_role_permission(self, db: AsyncSession, pairs: list[tuple[int, int]]) -> None:
        """批量添加角色-权限关联"""
        for role_id, permission_id in set(pairs):
            await db.execute(
                text(
                    """INSERT OR IGNORE INTO role_permission_rel (role_id, permission_id)
                       VALUES (:role_id, :permission_id)"""
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )

    async def remove_role_permission(self, db: AsyncSession, pairs: list[tuple[int, int]]) -> None:
        """批量删除角色-权限关联"""
        for role_id, permission_id in set(pairs):
            await db.execute(
                text(
                    """DELETE FROM role_permission_rel
                       WHERE role_id = :role_id AND permission_id = :permission_id"""
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )
