"""会话生命周期锁冲突处理测试。"""

import unittest
from contextlib import asynccontextmanager
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.errors import ConversationBusyError
from app.shared.clients.postgres_advisory_locks import PostgresAdvisoryLocks
from app.shared.errors.infrastructure import AdvisoryLockBusyError

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
    MagicMock,
]:
    """创建只会走锁冲突分支的生命周期服务。"""
    agents = MagicMock()
    runs = MagicMock(stop=AsyncMock())
    postgres = MagicMock()
    repo = MagicMock(get=AsyncMock(return_value=MagicMock()))
    postgres.session.return_value.__aenter__.return_value = MagicMock()
    service = ConversationLifecycleService(
        postgres=postgres,
        lock_provider=MagicMock(
            spec=PostgresAdvisoryLocks,
            advisory_lock=_BusyLockProvider().advisory_lock,
        ),
        agents=agents,
        sandbox=MagicMock(),
        config=MagicMock(),
        runs=runs,
    )
    return service, runs.stop, postgres, repo


class ConversationLifecycleBusyTest(unittest.IsolatedAsyncioTestCase):
    async def test_deletion_request_translates_busy_advisory_lock(self) -> None:
        service, cancel_execution, postgres, repo = _build_service()

        with (
            patch(
                "app.assistant.conversations.lifecycle.ConversationPGRepo",
                return_value=repo,
            ),
            self.assertRaises(ConversationBusyError) as caught,
        ):
            await service.request_conversation_deletion(1, _CONVERSATION_ID)

        self.assertIsInstance(caught.exception.__cause__, AdvisoryLockBusyError)
        self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(caught.exception.detail, "对话正在运行或清理，请稍后重试")
        cancel_execution.assert_awaited_once_with(1, _CONVERSATION_ID)
        postgres.session.assert_called_once()

    async def test_physical_cleanup_keeps_lock_error_for_task_retry(self) -> None:
        service, _, postgres, _ = _build_service()

        with self.assertRaises(AdvisoryLockBusyError):
            await service.delete_conversation_resources(1, _CONVERSATION_ID)

        postgres.session.assert_not_called()

    async def test_non_draft_deletion_does_not_stop_active_run(self):
        service, stop, _, repo = _build_service()
        repo.get.return_value.is_draft = False
        with patch(
            "app.assistant.conversations.lifecycle.ConversationPGRepo",
            return_value=repo,
        ):
            self.assertFalse(
                await service.request_conversation_deletion(
                    1, _CONVERSATION_ID, draft_only=True
                )
            )
        stop.assert_not_awaited()


class ConversationCleanupTransactionTest(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_releases_transactions_before_external_deletion(self):
        await self._check_cleanup(False)

    async def test_cleanup_failure_keeps_directory_for_retry(self):
        await self._check_cleanup(True)

    async def _check_cleanup(self, fails):
        """外部资源删除使用已释放的数据库会话，失败时保留目录供重试。"""
        active_sessions = 0
        committed = []

        @asynccontextmanager
        async def transaction():
            yield
            committed.append(True)

        @asynccontextmanager
        async def session():
            nonlocal active_sessions
            active_sessions += 1
            try:
                yield MagicMock(begin=transaction)
            finally:
                active_sessions -= 1

        @asynccontextmanager
        async def lock(*args):
            yield

        async def external_delete(*args):
            self.assertEqual(active_sessions, 0)

        async def delete_files(*args):
            await external_delete()
            self.assertEqual(len(committed), 2)
            if fails:
                raise RuntimeError("files")

        repo = MagicMock(get=AsyncMock(return_value=MagicMock()), delete=AsyncMock())
        recalls = MagicMock(delete_all=AsyncMock())
        service = ConversationLifecycleService(
            MagicMock(session=session),
            MagicMock(advisory_lock=lock),
            MagicMock(
                delete_agent_under_lifecycle_lock=AsyncMock(side_effect=external_delete)
            ),
            MagicMock(delete_conversation=AsyncMock(side_effect=delete_files)),
            MagicMock(),
        )
        with (
            patch(
                "app.assistant.conversations.lifecycle.ConversationPGRepo",
                return_value=repo,
            ),
            patch(
                "app.assistant.recall.cleanup.SemanticRecallPGRepo",
                return_value=recalls,
            ),
        ):
            if fails:
                with pytest.raises(RuntimeError, match="files"):
                    await service.delete_conversation_resources(1, _CONVERSATION_ID)
                repo.delete.assert_not_awaited()
            else:
                self.assertTrue(
                    await service.delete_conversation_resources(1, _CONVERSATION_ID)
                )
                repo.delete.assert_awaited_once_with(1, _CONVERSATION_ID)
        self.assertEqual(active_sessions, 0)
        self.assertEqual(len(committed), 2 if fails else 3)
