"""会话生命周期编排测试"""

import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.analytics.models import ConversationInfo
from app.analytics.services.conversation_lifecycle import ConversationLifecycleService
from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.config.app_config import LifecycleConfig


class FakePersistenceManager:
    def __init__(self) -> None:
        self.store = MagicMock()

    def get_store(self) -> MagicMock:
        return self.store

    @asynccontextmanager
    async def advisory_lock(
        self,
        name: str,
        *,
        timeout: float,
    ) -> AsyncGenerator[None]:
        del name, timeout
        yield


def build_config() -> LifecycleConfig:
    return LifecycleConfig(
        draft_ttl_minutes=60,
        cleanup_interval_seconds=30,
        cleanup_batch_size=100,
        user_deletion_retry_seconds=10,
    )


def build_conversation(*, is_draft: bool, updated_at: datetime) -> ConversationInfo:
    return ConversationInfo(
        id=uuid4(),
        user_id=7,
        title="新对话",
        title_pending=True,
        is_draft=is_draft,
        create_at=updated_at,
        update_at=updated_at,
    )


class ConversationLifecycleServiceTest(unittest.IsolatedAsyncioTestCase):
    def build_service(
        self,
    ) -> tuple[
        ConversationLifecycleService,
        MagicMock,
        MagicMock,
        FakePersistenceManager,
    ]:
        persistence = FakePersistenceManager()
        agents = MagicMock()
        agents.delete_agent_under_lifecycle_lock = AsyncMock()
        agents.delete_user_agents = AsyncMock()
        sandbox = MagicMock()
        sandbox.delete_conversation = AsyncMock()
        return (
            ConversationLifecycleService(
                cast(LangGraphPostgresManager, persistence),
                agents,
                sandbox,
                build_config(),
            ),
            agents,
            sandbox,
            persistence,
        )

    async def test_expired_draft_deletes_all_conversation_resources(self) -> None:
        service, agents, sandbox, _ = self.build_service()
        cutoff = datetime.now(UTC)
        conversation = build_conversation(
            is_draft=True,
            updated_at=cutoff - timedelta(minutes=1),
        )
        conversation_repo = MagicMock()
        conversation_repo.get = AsyncMock(return_value=conversation)
        conversation_repo.delete = AsyncMock()
        recall_repo = MagicMock()
        recall_repo.delete_all = AsyncMock()

        with (
            patch(
                "app.analytics.services.conversation_lifecycle.ConversationPGRepo",
                return_value=conversation_repo,
            ),
            patch(
                "app.analytics.services.conversation_lifecycle.SemanticRecallPGRepo",
                return_value=recall_repo,
            ),
        ):
            deleted = await service.delete_conversation(
                conversation.user_id,
                conversation.id,
                draft_expired_before=cutoff,
            )

        self.assertTrue(deleted)
        agents.delete_agent_under_lifecycle_lock.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        recall_repo.delete_all.assert_awaited_once_with(
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
        service, agents, sandbox, _ = self.build_service()
        cutoff = datetime.now(UTC)
        conversation = build_conversation(
            is_draft=False,
            updated_at=cutoff - timedelta(days=1),
        )
        conversation_repo = MagicMock()
        conversation_repo.get = AsyncMock(return_value=conversation)

        with patch(
            "app.analytics.services.conversation_lifecycle.ConversationPGRepo",
            return_value=conversation_repo,
        ):
            deleted = await service.delete_conversation(
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
        service, agents, sandbox, _ = self.build_service()
        conversation = build_conversation(
            is_draft=False,
            updated_at=datetime.now(UTC),
        )
        conversation_repo = MagicMock()
        conversation_repo.get = AsyncMock(return_value=conversation)

        with patch(
            "app.analytics.services.conversation_lifecycle.ConversationPGRepo",
            return_value=conversation_repo,
        ):
            deleted = await service.delete_conversation(
                conversation.user_id,
                conversation.id,
                draft_only=True,
            )

        self.assertFalse(deleted)
        agents.delete_agent_under_lifecycle_lock.assert_not_awaited()
        sandbox.delete_conversation.assert_not_awaited()

    async def test_user_cleanup_repeats_until_catalog_is_empty(self) -> None:
        service, agents, _, _ = self.build_service()
        conversation = build_conversation(
            is_draft=True,
            updated_at=datetime.now(UTC),
        )
        conversation_repo = MagicMock()
        conversation_repo.list_all_by_user = AsyncMock(
            side_effect=[[conversation], []]
        )
        recall_repo = MagicMock()
        recall_repo.delete_all_by_user = AsyncMock()

        with (
            patch(
                "app.analytics.services.conversation_lifecycle.ConversationPGRepo",
                return_value=conversation_repo,
            ),
            patch(
                "app.analytics.services.conversation_lifecycle.SemanticRecallPGRepo",
                return_value=recall_repo,
            ),
            patch.object(
                service,
                "delete_conversation",
                new=AsyncMock(return_value=True),
            ) as delete_conversation,
        ):
            await service.delete_user_conversations(conversation.user_id)

        delete_conversation.assert_awaited_once_with(
            conversation.user_id,
            conversation.id,
        )
        agents.delete_user_agents.assert_awaited_once_with(conversation.user_id)
        recall_repo.delete_all_by_user.assert_awaited_once_with(
            conversation.user_id
        )


if __name__ == "__main__":
    unittest.main()
