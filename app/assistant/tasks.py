"""应用内会话标题、资源删除与定时清理任务。"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from uuid import UUID

from loguru import logger

from app.assistant.services.lifecycle import ConversationLifecycleService
from app.assistant.services.title import ConversationTitleService
from app.shared.clients.postgres_client_manager import PostgresClientManager

_CLEANUP_INTERVAL_SECONDS = 300
_TASK_TIMEOUT_SECONDS = 3600


class ConversationTasks:
    """在 Web 事件循环中执行任务，并在资源关闭前取消和回收任务。"""

    def __init__(
        self,
        postgres: PostgresClientManager,
        conversations: ConversationLifecycleService,
    ) -> None:
        self._postgres = postgres
        self._conversations = conversations
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    def start(self) -> None:
        """启动周期清理，首轮立即扫描持久化的待删除记录。"""
        task = asyncio.create_task(self._cleanup_loop(), name="conversation-cleanup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _submit(
        self, name: str, operation: Callable[[], Coroutine[object, object, object]]
    ) -> None:
        if self._closing:
            raise RuntimeError("后台任务服务正在关闭")
        coroutine = operation()
        try:
            task = asyncio.create_task(coroutine, name=name)
        except Exception:
            coroutine.close()
            raise
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, name: str, operation: Callable[[], Awaitable[object]]) -> None:
        """执行资源清理；失败后最多重试三次，取消直接向上传播。"""
        for attempt in range(4):
            try:
                async with asyncio.timeout(_TASK_TIMEOUT_SECONDS):
                    await operation()
                return
            except Exception:  # noqa: BLE001
                logger.exception("后台任务失败: name={}, attempt={}", name, attempt + 1)
                if attempt < 3:
                    await asyncio.sleep(2**attempt)

    def generate_title(
        self, user_id: int, conversation_id: UUID, user_text: str
    ) -> None:
        """后台生成标题；关闭期间跳过，失败保留即时标题，不重试。"""
        if self._closing:
            return

        async def generate() -> None:
            try:
                async with asyncio.timeout(_TASK_TIMEOUT_SECONDS):
                    await ConversationTitleService(self._postgres).generate_and_update(
                        user_id, conversation_id, user_text
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "生成会话标题失败，保留即时标题: conversation_id={}",
                    conversation_id,
                )

        self._submit(f"conversation-title:{conversation_id}", generate)

    def delete_conversation(self, user_id: int, conversation_id: UUID) -> None:
        """提交会话物理资源清理；持久化删除标记用于后续补偿。"""
        name = f"conversation-delete:{conversation_id}"
        self._submit(
            name,
            partial(
                self._run,
                name,
                partial(
                    self._conversations.delete_conversation_resources,
                    user_id,
                    conversation_id,
                ),
            ),
        )

    async def _cleanup_loop(self) -> None:
        """在当前 Web 进程中串行执行周期扫描。"""
        while True:
            try:
                await self._run(
                    "pending-deletions",
                    self._conversations.cleanup_pending_deletions,
                )
                await self._run(
                    "expired-drafts", self._conversations.cleanup_expired_drafts
                )
            except Exception:  # noqa: BLE001
                logger.exception("会话周期清理失败")
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)

    async def close(self) -> None:
        """取消并等待任务退出，随后应用可关闭数据库和沙箱。"""
        self._closing = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
