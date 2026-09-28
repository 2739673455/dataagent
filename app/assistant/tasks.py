"""应用内会话标题、资源删除与定时清理任务。"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from loguru import logger

from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.conversations.title import ConversationTitleService
from app.assistant.model_factory import create_configured_model
from app.assistant.repositories.conversation import ConversationPGRepo
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import LifecycleConfig, cfg
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class ConversationTasks:
    """在 Web 事件循环中执行任务，并在资源关闭前取消和回收任务。"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        conversations: ConversationLifecycleService,
        persistence: LangGraphPostgresManager,
        config: LifecycleConfig,
    ) -> None:
        self._postgres = postgres
        self._conversations = conversations
        self._persistence = persistence
        self._config = config
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    def start(self) -> None:
        """启动周期清理，首轮立即扫描持久化的待删除记录。"""
        task = asyncio.create_task(self._cleanup_loop(), name="conversation-cleanup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _submit(self, name: str, operation: Callable[[], Awaitable[object]]) -> None:
        if self._closing:
            raise RuntimeError("后台任务服务正在关闭")
        task = asyncio.create_task(self._run(name, operation), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, name: str, operation: Callable[[], Awaitable[object]]) -> None:
        """执行任务；失败后最多重试三次，取消直接向上传播。"""
        for attempt in range(4):
            try:
                async with asyncio.timeout(self._config.task_timeout_seconds):
                    await operation()
                return
            except Exception:  # noqa: BLE001
                logger.exception("后台任务失败: name={}, attempt={}", name, attempt + 1)
                if attempt < 3:
                    await asyncio.sleep(2**attempt)

    def generate_title(
        self, user_id: int, conversation_id: UUID, expected_title: str, user_text: str
    ) -> None:
        """提交标题生成，数据库条件更新保护已经变更的标题。"""
        self._submit(
            f"conversation-title:{conversation_id}",
            partial(
                self._generate_title,
                user_id,
                conversation_id,
                expected_title,
                user_text,
            ),
        )

    async def _generate_title(
        self, user_id: int, conversation_id: UUID, expected_title: str, user_text: str
    ) -> None:
        async with (
            create_configured_model(cfg.lm_config.active) as model,
            self._postgres.session() as session,
        ):
            await ConversationTitleService(model).generate_and_update(
                ConversationPGRepo(session),
                user_id,
                conversation_id,
                expected_title,
                user_text,
            )
            await session.commit()

    def delete_conversation(self, user_id: int, conversation_id: UUID) -> None:
        """提交会话物理资源清理；持久化删除标记用于后续补偿。"""
        self._submit(
            f"conversation-delete:{conversation_id}",
            partial(
                self._conversations.delete_conversation_resources,
                user_id,
                conversation_id,
            ),
        )

    async def _cleanup_loop(self) -> None:
        """使用数据库互斥锁协调多个 API 进程的周期扫描。"""
        while True:
            try:
                async with self._persistence.advisory_lock("conversation-cleanup"):
                    await self._run(
                        "pending-deletions",
                        self._conversations.cleanup_pending_deletions,
                    )
                    await self._run(
                        "expired-drafts", self._conversations.cleanup_expired_drafts
                    )
            except AdvisoryLockBusyError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("会话周期清理失败")
            await asyncio.sleep(self._config.cleanup_interval_seconds)

    async def close(self) -> None:
        """取消并等待任务退出，随后应用可关闭数据库和沙箱。"""
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
