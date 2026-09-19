"""跨存储用户注销编排。"""

import asyncio
from datetime import UTC, datetime

from loguru import logger

from app.assistant.conversations.lifecycle import (
    ConversationLifecycleService,
)
from app.identity import errors as auth_error
from app.identity.services.user_deletion_store import PostgresUserDeletionStateStore
from app.sandbox.manager import DockerSandboxManager
from app.workflows.task_scheduler import enqueue_user_deletion


class UserDeletionService:
    """协调注销状态、会话资源与用户沙箱清理。"""

    def __init__(
        self,
        state_store: PostgresUserDeletionStateStore,
        sandbox: DockerSandboxManager,
        conversations: ConversationLifecycleService,
    ) -> None:
        """绑定用户注销涉及的各存储和生命周期服务。"""
        self._state_store = state_store
        self._sandbox = sandbox
        self._conversations = conversations

    async def request_deletion(self, user_id: int, *, operator_id: int) -> bool:
        """提交注销事务后立即投递清理，投递失败由数据库补偿扫描恢复。"""
        if user_id == operator_id:
            raise auth_error.InvalidUserMutationError(
                detail="不能注销当前操作的管理员账号"
            )

        submitted = await self._state_store.request(user_id, datetime.now(UTC))
        if submitted:
            logger.info(f"用户注销已受理: operator_id={operator_id}, user_id={user_id}")
            try:
                await asyncio.to_thread(enqueue_user_deletion, user_id)
            except Exception:  # noqa: BLE001
                # 已提交的禁用和 pending 记录仍有效，保留到期时间供补偿扫描领取。
                logger.exception(
                    f"用户注销立即投递失败，等待补偿扫描: user_id={user_id}"
                )
        return submitted

    async def process(self, user_id: int) -> None:
        """在任务入口持有用户锁并确认 pending 后，依次完成跨存储清理。"""
        await self._conversations.delete_user_conversations(user_id)
        await self._sandbox.delete_user_sandbox(user_id)
        await self._state_store.complete(user_id, datetime.now(UTC))
        logger.info(f"用户注销清理编排完成: user_id={user_id}")
