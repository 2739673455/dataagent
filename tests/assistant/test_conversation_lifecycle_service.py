"""会话生命周期编排测试"""

import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.assistant.models.conversation import Conversation
from app.assistant.services.conversation_lifecycle import ConversationLifecycleService
from app.shared.config.app_config import LifecycleConfig


class FakePersistenceManager:
    @asynccontextmanager
    async def advisory_lock(
        self,
        name: str,
    ) -> AsyncGenerator[None]:
        del name
        yield


def build_config() -> LifecycleConfig:
    return LifecycleConfig(
        draft_ttl_minutes=60,
        cleanup_batch_size=100,
        user_deletion_retry_seconds=10,
    )


def build_conversation(*, is_draft: bool, updated_at: datetime) -> Conversation:
    return Conversation(
        id=uuid4(),
        user_id=7,
        title="新对话",
        title_pending=True,
        is_draft=is_draft,
        create_at=updated_at,
        update_at=updated_at,
    )


@asynccontextmanager
async def conversation_repository(
    repository: MagicMock,
) -> AsyncGenerator[MagicMock]:
    """提供测试使用的会话数据访问上下文"""
    yield repository


@asynccontextmanager
async def recall_repository(
    repository: MagicMock,
) -> AsyncGenerator[MagicMock]:
    """提供测试使用的语义召回数据访问上下文"""
    yield repository


class ConversationLifecycleServiceTest(unittest.IsolatedAsyncioTestCase):
    def build_service(
        self,
    ) -> tuple[
        ConversationLifecycleService,
        MagicMock,
        MagicMock,
        MagicMock,
        MagicMock,
    ]:
        persistence = FakePersistenceManager()
        conversation_repo = MagicMock()
        recall_cleaner = MagicMock()
        agents = MagicMock()
        agents.cancel_agent_execution = AsyncMock()
        agents.delete_agent_under_lifecycle_lock = AsyncMock()
        agents.delete_user_agents = AsyncMock()
        sandbox = MagicMock()
        sandbox.delete_conversation = AsyncMock()
        return (
            ConversationLifecycleService(
                lambda: conversation_repository(conversation_repo),
                lambda: recall_repository(recall_cleaner),
                persistence,
                agents,
                sandbox,
                build_config(),
            ),
            agents,
            sandbox,
            conversation_repo,
            recall_cleaner,
        )

    async def test_expired_draft_deletes_all_conversation_resources(self) -> None:
        service, agents, sandbox, conversation_repo, recall_cleaner = (
            self.build_service()
        )
        cutoff = datetime.now(UTC)
        conversation = build_conversation(
            is_draft=True,
            updated_at=cutoff - timedelta(minutes=1),
        )
        conversation_repo.get = AsyncMock(return_value=conversation)
        conversation_repo.delete = AsyncMock()
        recall_cleaner.delete_all = AsyncMock()

        deleted = await service.delete_conversation_resources(
            conversation.user_id,
            conversation.id,
            draft_expired_before=cutoff,
        )

        self.assertTrue(deleted)
        agents.delete_agent_under_lifecycle_lock.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        recall_cleaner.delete_all.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        sandbox.delete_conversation.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        conversation_repo.delete.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )

    async def test_formal_conversation_is_not_deleted_by_draft_cleanup(self) -> None:
        service, agents, sandbox, conversation_repo, _ = self.build_service()
        cutoff = datetime.now(UTC)
        conversation = build_conversation(
            is_draft=False,
            updated_at=cutoff - timedelta(days=1),
        )
        conversation_repo.get = AsyncMock(return_value=conversation)

        deleted = await service.delete_conversation_resources(
            conversation.user_id,
            conversation.id,
            draft_expired_before=cutoff,
        )

        self.assertFalse(deleted)
        agents.delete_agent_under_lifecycle_lock.assert_not_awaited()
        sandbox.delete_conversation.assert_not_awaited()

    async def test_delayed_draft_delete_does_not_delete_formal_conversation(
        self,
    ) -> None:
        service, agents, sandbox, conversation_repo, _ = self.build_service()
        conversation = build_conversation(
            is_draft=False,
            updated_at=datetime.now(UTC),
        )
        conversation_repo.get = AsyncMock(return_value=conversation)

        deleted = await service.delete_conversation_resources(
            conversation.user_id,
            conversation.id,
            draft_only=True,
        )

        self.assertFalse(deleted)
        agents.delete_agent_under_lifecycle_lock.assert_not_awaited()
        sandbox.delete_conversation.assert_not_awaited()

    async def test_user_cleanup_repeats_until_catalog_is_empty(self) -> None:
        service, agents, _, conversation_repo, recall_cleaner = self.build_service()
        conversation = build_conversation(
            is_draft=True,
            updated_at=datetime.now(UTC),
        )
        conversation_repo.list_all_by_user = AsyncMock(side_effect=[[conversation], []])
        recall_cleaner.delete_all_by_user = AsyncMock()

        with patch.object(
            service,
            "delete_conversation_resources",
            new=AsyncMock(return_value=True),
        ) as delete_conversation_resources:
            await service.delete_user_conversations(conversation.user_id)

        delete_conversation_resources.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        agents.delete_user_agents.assert_awaited_once_with(conversation.user_id)
        recall_cleaner.delete_all_by_user.assert_awaited_once_with(conversation.user_id)


if __name__ == "__main__":
    unittest.main()
