"""会话用例保证事务提交后调度，历史查询先验证用户归属。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.assistant.contracts import CreateConversationRequest
from app.assistant.conversations.service import ConversationService
from app.assistant.errors import ConversationNotFoundError


def test_creation_commits_before_title_scheduling_and_survives_broker_failure():
    events = []
    conversation = SimpleNamespace(
        id=uuid4(), title="分析", update_at=datetime.now(UTC), is_draft=False
    )

    @asynccontextmanager
    async def transaction():
        events.append("begin")
        yield
        events.append("commit")

    def submit(*args):
        assert events == ["begin", "commit"]
        raise RuntimeError("broker unavailable")

    repository = MagicMock(
        session=MagicMock(begin=transaction),
        create=AsyncMock(return_value=conversation),
    )
    service = ConversationService(
        repository, MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    with patch(
        "app.assistant.conversations.service.enqueue_conversation_title",
        side_effect=submit,
    ):
        result = asyncio.run(
            service.create(7, CreateConversationRequest(initial_message="分析"))
        )
    assert result.conversation_id == conversation.id
    assert result.running is False
    repository.create.assert_awaited_once_with(7, "分析", is_draft=False)


def test_history_never_reads_checkpoint_when_conversation_is_not_owned():
    reader = MagicMock(read_planner_state=AsyncMock())
    service = ConversationService(
        MagicMock(get=AsyncMock(return_value=None)),
        MagicMock(),
        reader,
        MagicMock(),
        MagicMock(),
    )
    with pytest.raises(ConversationNotFoundError):
        asyncio.run(service.messages(7, uuid4()))
    reader.read_planner_state.assert_not_awaited()
