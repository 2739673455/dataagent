"""会话生命周期锁冲突处理测试。"""

import unittest
from contextlib import asynccontextmanager
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from app.assistant.conversations.lifecycle import (
    ConversationLifecycleService,
)
from app.assistant.errors import ConversationBusyError
from app.shared.clients.langgraph_postgres_manager import AdvisoryLockBusyError

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


class _BusyLockProvider:
    """始终报告咨询锁被占用。"""

    @asynccontextmanager
    async def advisory_lock(self, name: str):
        """在进入锁上下文时报告占用。"""
        raise AdvisoryLockBusyError(f"咨询锁正在使用: {name}")
        yield


def _build_service() -> tuple[
    ConversationLifecycleService,
    AsyncMock,
    MagicMock,
]:
    """创建只会走锁冲突分支的生命周期服务。"""
    agents = MagicMock()
    runs = MagicMock(stop=AsyncMock())
    repository_factory = MagicMock()
    repository_factory.return_value.__aenter__.return_value.get = AsyncMock(
        return_value=MagicMock()
    )
    service = ConversationLifecycleService(
        repository_factory=repository_factory,
        recall_cleaner=MagicMock(),
        lock_provider=_BusyLockProvider(),
        agents=agents,
        sandbox=MagicMock(),
        config=MagicMock(),
        runs=runs,
    )
    return service, runs.stop, repository_factory


class ConversationLifecycleBusyTest(unittest.IsolatedAsyncioTestCase):
    async def test_deletion_request_translates_busy_advisory_lock(self) -> None:
        service, cancel_execution, repository_factory = _build_service()

        with self.assertRaises(ConversationBusyError) as caught:
            await service.request_conversation_deletion(1, _CONVERSATION_ID)

        self.assertIsInstance(caught.exception.__cause__, AdvisoryLockBusyError)
        self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(caught.exception.detail, "对话正在运行或清理，请稍后重试")
        cancel_execution.assert_awaited_once_with(1, _CONVERSATION_ID)
        repository_factory.assert_called_once()

    async def test_physical_cleanup_keeps_lock_error_for_task_retry(self) -> None:
        service, _, repository_factory = _build_service()

        with self.assertRaises(AdvisoryLockBusyError):
            await service.delete_conversation_resources(1, _CONVERSATION_ID)

        repository_factory.assert_not_called()

    async def test_non_draft_deletion_does_not_stop_active_run(self):
        service, stop, repository_factory = _build_service()
        repository_factory.return_value.__aenter__.return_value.get.return_value.is_draft = False
        self.assertFalse(
            await service.request_conversation_deletion(
                1, _CONVERSATION_ID, draft_only=True
            )
        )
        stop.assert_not_awaited()
