"""历史会话管理权限与标题协议测试"""

import unittest
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.analytics import errors as chat_error
from app.analytics.api.attachment.router import (
    api_delete_attachment,
    api_upload_attachment,
)
from app.analytics.api.chat.router import (
    api_get_subagent_messages,
    api_update_conversation,
)
from app.analytics.api.chat.schemas import UpdateConversationRequest
from app.identity.api.auth.dependencies import AnalysisUserDep, CurrentUserDep


class ChatHistoryAccessTest(unittest.TestCase):
    """验证历史数据管理不依赖可用 Doris 角色"""

    def test_title_is_trimmed_and_limited(self) -> None:
        conversation_id = uuid4()

        request = UpdateConversationRequest(
            conversation_id=conversation_id,
            title="  月度销售分析  ",
        )

        self.assertEqual(request.title, "月度销售分析")
        with self.assertRaises(ValidationError):
            UpdateConversationRequest(conversation_id=conversation_id, title="   ")
        with self.assertRaises(ValidationError):
            UpdateConversationRequest(conversation_id=conversation_id, title="x" * 65)

    def test_historical_mutations_only_require_login(self) -> None:
        update_hints = get_type_hints(api_update_conversation, include_extras=True)
        delete_hints = get_type_hints(api_delete_attachment, include_extras=True)
        subagent_hints = get_type_hints(
            api_get_subagent_messages,
            include_extras=True,
        )

        self.assertEqual(update_hints["current_user"], CurrentUserDep)
        self.assertEqual(delete_hints["current_user"], CurrentUserDep)
        self.assertEqual(subagent_hints["current_user"], CurrentUserDep)

    def test_new_attachment_still_requires_analysis_access(self) -> None:
        upload_hints = get_type_hints(api_upload_attachment, include_extras=True)

        self.assertEqual(upload_hints["current_user"], AnalysisUserDep)


class SubagentHistoryAccessTest(unittest.IsolatedAsyncioTestCase):
    async def test_subagent_history_requires_owned_conversation(self) -> None:
        repository = MagicMock()
        repository.get = AsyncMock(return_value=None)
        agents = MagicMock()

        with self.assertRaises(chat_error.ConversationNotFoundError):
            await api_get_subagent_messages(
                uuid4(),
                "sales",
                "explorer",
                "source",
                "delegation-1",
                repository,
                MagicMock(id=7),
                agents,
            )

        agents.get_conversation_runtime.assert_not_called()

    async def test_missing_delegation_has_specific_not_found_error(self) -> None:
        repository = MagicMock()
        repository.get = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "app.analytics.api.chat.router.chat_service.get_subagent_activity",
                new=AsyncMock(return_value=None),
            ),
            self.assertRaises(chat_error.SubagentRunNotFoundError),
        ):
            await api_get_subagent_messages(
                uuid4(),
                "sales",
                "analyst",
                "review",
                "delegation-missing",
                repository,
                MagicMock(id=7),
                MagicMock(),
            )


if __name__ == "__main__":
    unittest.main()
