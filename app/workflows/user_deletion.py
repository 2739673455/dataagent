"""跨存储用户注销编排。"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from app.assistant.services.conversation_lifecycle import (
    ConversationLifecycleService,
)
from app.identity import errors as auth_error
from app.shared.config.app_config import LifecycleConfig
from app.workflows.contracts import (
    UserDeletionStateStore,
    UserSandboxCleaner,
)


class UserDeletionService:
    """协调认证库、会话库、元数据库、索引和沙箱的用户注销。"""

    def __init__(
        self,
        state_store: UserDeletionStateStore,
        sandbox: UserSandboxCleaner,
        conversations: ConversationLifecycleService,
        config: LifecycleConfig,
    ) -> None:
        """绑定用户注销涉及的各存储和生命周期服务。"""
        self._state_store = state_store
        self._sandbox = sandbox
        self._conversations = conversations
        self._config = config

    async def request_deletion(self, user_id: int, *, operator_id: int) -> bool:
        """禁用目标用户并持久化注销任务。"""
        if user_id == operator_id:
            raise auth_error.InvalidUserMutationError(
                detail="不能注销当前操作的管理员账号"
            )

        submitted = await self._state_store.request(user_id, datetime.now(UTC))
        if submitted:
            logger.info(f"用户注销已受理: operator_id={operator_id}, user_id={user_id}")
        return submitted

    async def process(self, user_id: int) -> None:
        """幂等执行一个用户的跨存储注销清理。"""
        if await self._state_store.is_completed(user_id):
            logger.info(f"用户注销清理已完成，跳过重复任务: user_id={user_id}")
            return
        logger.info(f"开始用户注销清理编排: user_id={user_id}")
        try:
            await self._conversations.delete_user_conversations(user_id)
            logger.info(f"用户会话资源清理完成: user_id={user_id}")
            await self._sandbox.delete_user_sandbox(user_id)
            logger.info(f"用户沙箱资源清理完成: user_id={user_id}")
            await self._state_store.complete(user_id, datetime.now(UTC))
            logger.info(f"用户注销清理编排完成: user_id={user_id}")
        except Exception as exc:
            await self._record_failure(user_id, exc)
            logger.exception(
                "用户注销清理编排失败: "
                f"user_id={user_id}, error_type={type(exc).__name__}"
            )
            raise

    async def _record_failure(self, user_id: int, exc: Exception) -> None:
        """记录注销失败原因和下一次重试时间。"""
        now = datetime.now(UTC)
        next_attempt_at = now + timedelta(
            seconds=self._config.user_deletion_retry_seconds
        )
        await self._state_store.record_failure(
            user_id,
            error=f"{type(exc).__name__}: {exc}",
            next_attempt_at=next_attempt_at,
        )
        logger.warning(
            "用户注销失败状态已记录: "
            f"user_id={user_id}, error_type={type(exc).__name__}, "
            f"next_attempt_at={next_attempt_at.isoformat()}"
        )
