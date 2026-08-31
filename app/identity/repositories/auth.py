"""PostgreSQL 认证身份与 Doris 权限投影访问"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.account import RefreshToken, User
from app.identity.models.doris import DorisRoleAssetGrant
from app.identity.models.lifecycle import UserDeletionTask

_SECURITY_MUTATION_LOCK_KEY = 0x444154414147454E


class AuthPGRepo:
    """PostgreSQL 认证身份与 Doris 权限投影存储"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求使用的异步数据库会话"""
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """返回当前存储绑定的数据库会话"""
        return self._session

    async def lock_security_mutation(self) -> None:
        """串行化用户身份与跨数据库权限变更"""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _SECURITY_MUTATION_LOCK_KEY},
        )

    async def count_admins(self) -> int:
        """统计当前平台管理员数量"""
        return int(
            await self._session.scalar(
                select(func.count(User.id)).where(
                    User.is_admin.is_(True),
                    User.is_active.is_(True),
                )
            )
            or 0
        )

    async def add_user(self, user: User) -> User:
        """新增用户并分配主键"""
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete_user(self, user: User) -> None:
        """删除用户记录"""
        await self._session.delete(user)
        await self._session.flush()

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取用户"""
        return await self._session.scalar(
            select(User)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )

    async def get_user_by_id_for_update(self, user_id: int) -> User | None:
        """锁定并按主键读取用户"""
        return await self._session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )

    async def get_user_by_email_for_update(self, email: str) -> User | None:
        """锁定并按规范化邮箱读取用户"""
        return await self._session.scalar(
            select(User).where(User.email == email).with_for_update()
        )

    async def get_user_by_username_for_update(self, username: str) -> User | None:
        """锁定并按规范化用户名读取用户"""
        return await self._session.scalar(
            select(User).where(User.username == username).with_for_update()
        )

    async def get_user_by_email(self, email: str) -> User | None:
        """按规范化邮箱读取用户"""
        return await self._session.scalar(select(User).where(User.email == email))

    async def get_user_by_username(self, username: str) -> User | None:
        """按规范化用户名读取用户"""
        return await self._session.scalar(select(User).where(User.username == username))

    async def list_users(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
    ) -> list[User]:
        """分页读取用户，支持用户名与邮箱搜索"""
        stmt = select(User)
        if query:
            stmt = stmt.where(
                or_(
                    User.username.icontains(query, autoescape=True),
                    User.email.icontains(query, autoescape=True),
                )
            )
        result = await self._session.scalars(
            stmt.order_by(User.id).limit(limit).offset(offset)
        )
        return list(result)

    async def count_users(self, *, query: str | None = None) -> int:
        """统计用户总量，支持用户名与邮箱搜索"""
        stmt = select(func.count(User.id))
        if query:
            stmt = stmt.where(
                or_(
                    User.username.icontains(query, autoescape=True),
                    User.email.icontains(query, autoescape=True),
                )
            )
        return int(await self._session.scalar(stmt) or 0)

    async def set_user_active(self, user: User, is_active: bool) -> None:
        """设置用户启用状态"""
        user.is_active = is_active
        await self._session.flush()

    async def set_user_password(self, user: User, password_hash: str) -> None:
        """更新密码哈希并推进认证版本"""
        user.password_hash = password_hash
        user.auth_version += 1
        await self._session.flush()

    async def update_user(
        self,
        user: User,
        *,
        username: str | None = None,
        email: str | None = None,
        password_hash: str | None = None,
        doris_role: str | None,
        update_doris_role: bool,
        is_admin: bool | None = None,
    ) -> None:
        """更新用户基础信息、角色与凭据"""
        if username is not None:
            user.username = username
        if email is not None:
            user.email = email
        if password_hash is not None:
            user.password_hash = password_hash
        if update_doris_role:
            user.doris_role_name = doris_role
        if is_admin is not None:
            user.is_admin = is_admin
        user.auth_version += 1
        await self._session.flush()

    async def set_user_doris_role(self, user: User, role_name: str) -> None:
        """替换用户唯一 Doris 角色"""
        user.doris_role_name = role_name
        await self._session.flush()

    async def set_user_admin(self, user: User, is_admin: bool) -> None:
        """设置平台管理员标志"""
        user.is_admin = is_admin
        await self._session.flush()

    async def add_refresh_token(self, token: RefreshToken) -> None:
        """保存刷新令牌"""
        self._session.add(token)
        await self._session.flush()

    async def get_refresh_token_for_update(
        self,
        token_id: UUID,
    ) -> RefreshToken | None:
        """锁定并读取刷新令牌"""
        return await self._session.scalar(
            select(RefreshToken).where(RefreshToken.id == token_id).with_for_update()
        )

    @staticmethod
    def rotate_refresh_token(
        current: RefreshToken,
        replacement_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """标记刷新令牌已轮换"""
        current.revoked_at = revoked_at
        current.replaced_by_id = replacement_id

    async def revoke_refresh_family(
        self,
        family_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """吊销令牌族中全部有效令牌"""
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def revoke_user_refresh_tokens(
        self,
        user_id: int,
        revoked_at: datetime,
    ) -> None:
        """吊销用户的全部有效刷新令牌"""
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

    async def get_user_deletion_task(
        self,
        user_id: int,
    ) -> UserDeletionTask | None:
        """按用户读取注销任务"""
        return await self._session.get(UserDeletionTask, user_id)

    async def get_user_deletion_task_for_update(
        self,
        user_id: int,
    ) -> UserDeletionTask | None:
        """按用户读取并锁定待修改的注销任务"""
        return await self._session.get(
            UserDeletionTask,
            user_id,
            with_for_update=True,
        )

    async def enqueue_user_deletion(
        self,
        user_id: int,
        now: datetime,
    ) -> None:
        """新增或重新调度用户注销任务"""
        await self._session.execute(
            insert(UserDeletionTask)
            .values(
                user_id=user_id,
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
                last_error=None,
            )
            .on_conflict_do_update(
                index_elements=[UserDeletionTask.user_id],
                set_={
                    "status": "pending",
                    "next_attempt_at": now,
                    "last_error": None,
                    "updated_at": now,
                },
                where=UserDeletionTask.status != "completed",
            )
        )

    async def claim_due_user_deletions(
        self,
        now: datetime,
        *,
        lease_until: datetime,
        limit: int,
    ) -> list[UserDeletionTask]:
        """原子领取到期且未完成的用户注销任务"""
        result = await self._session.scalars(
            select(UserDeletionTask)
            .where(
                UserDeletionTask.status == "pending",
                UserDeletionTask.next_attempt_at <= now,
            )
            .order_by(UserDeletionTask.next_attempt_at, UserDeletionTask.user_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        tasks = list(result)
        for task in tasks:
            task.next_attempt_at = lease_until
        await self._session.flush()
        return tasks

    async def extend_user_deletion_claim(
        self,
        user_id: int,
        *,
        lease_until: datetime,
    ) -> bool:
        """延长一个未完成用户注销任务的领取租约"""
        task = await self._session.get(
            UserDeletionTask,
            user_id,
            with_for_update=True,
        )
        if task is None or task.status == "completed":
            return False
        task.next_attempt_at = lease_until
        await self._session.flush()
        return True

    async def record_user_deletion_failure(
        self,
        task: UserDeletionTask,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """记录注销失败并安排下一次重试"""
        task.attempt_count += 1
        task.last_error = error[:4000]
        task.next_attempt_at = next_attempt_at
        await self._session.flush()

    async def complete_user_deletion(
        self,
        task: UserDeletionTask,
        now: datetime,
    ) -> None:
        """标记用户注销任务完成"""
        task.status = "completed"
        task.last_error = None
        task.updated_at = now
        await self._session.flush()

    async def list_role_asset_grants(
        self,
        role_name: str,
    ) -> list[DorisRoleAssetGrant]:
        """读取指定 Doris 角色的权限投影"""
        result = await self._session.scalars(
            select(DorisRoleAssetGrant)
            .where(DorisRoleAssetGrant.role_name == role_name)
            .order_by(DorisRoleAssetGrant.resource_key)
        )
        return list(result)

    async def find_asset_grant(
        self,
        role_name: str,
        scope: str,
        resource_key: str,
    ) -> DorisRoleAssetGrant | None:
        """按角色和资产键读取权限投影"""
        return await self._session.scalar(
            select(DorisRoleAssetGrant).where(
                DorisRoleAssetGrant.role_name == role_name,
                DorisRoleAssetGrant.scope == scope,
                DorisRoleAssetGrant.resource_key == resource_key,
            )
        )

    async def add_asset_grant(
        self,
        grant: DorisRoleAssetGrant,
    ) -> DorisRoleAssetGrant:
        """新增 Doris 权限投影"""
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def delete_asset_grant(self, grant: DorisRoleAssetGrant) -> None:
        """删除 Doris 权限投影"""
        await self._session.delete(grant)
        await self._session.flush()

    async def delete_role_asset_grants(self, role_name: str) -> None:
        """删除指定 Doris 角色的全部权限投影"""
        await self._session.execute(
            delete(DorisRoleAssetGrant).where(
                DorisRoleAssetGrant.role_name == role_name
            )
        )
