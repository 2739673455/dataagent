"""历史会话管理权限与标题协议测试"""

import unittest
from typing import get_type_hints
from uuid import uuid4

from pydantic import ValidationError

from app.routes.api.v1.attachment.router import (
    api_delete_attachment,
    api_upload_attachment,
)
from app.routes.api.v1.auth.dependencies import AnalysisUserDep, CurrentUserDep
from app.routes.api.v1.chat.router import api_update_conversation
from app.routes.api.v1.chat.schemas import UpdateConversationRequest


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

        self.assertEqual(update_hints["current_user"], CurrentUserDep)
        self.assertEqual(delete_hints["current_user"], CurrentUserDep)

    def test_new_attachment_still_requires_analysis_access(self) -> None:
        upload_hints = get_type_hints(api_upload_attachment, include_extras=True)

        self.assertEqual(upload_hints["current_user"], AnalysisUserDep)


if __name__ == "__main__":
    unittest.main()
