"""跨模块工作流依赖协议"""

from datetime import datetime
from typing import Protocol


class UserDeletionStateStore(Protocol):
    """用户注销编排所需的认证状态存储能力"""

    async def request(self, user_id: int, requested_at: datetime) -> bool:
        """禁用用户、吊销令牌并创建注销任务"""
        ...

    async def is_completed(self, user_id: int) -> bool:
        """判断用户注销任务是否完成"""
        ...

    async def complete(self, user_id: int, completed_at: datetime) -> None:
        """删除认证用户并完成注销任务"""
        ...

    async def record_failure(
        self,
        user_id: int,
        *,
        error: str,
        next_attempt_at: datetime,
    ) -> None:
        """记录注销失败并安排重试"""
        ...

    async def list_due_user_ids(self, now: datetime, *, limit: int) -> list[int]:
        """列出已到执行时间的注销用户"""
        ...


class UserSandboxCleaner(Protocol):
    """用户注销所需的沙箱清理能力"""

    async def delete_user_sandbox(self, user_id: int) -> None:
        """删除用户全部沙箱资源"""
        ...


class UserQueryHistoryCleaner(Protocol):
    """用户注销所需的查询历史清理能力"""

    async def delete_user_query_history(self, user_id: int) -> None:
        """删除用户全部查询记录和查询经验"""
        ...
