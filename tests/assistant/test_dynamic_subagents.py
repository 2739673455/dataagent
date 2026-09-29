"""删除墓碑阻止会话重新构图。"""

import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.services.manager import AgentManager

_CONVERSATION_ID = uuid4()


class PlannerIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_tombstone_blocks_recreated_manager(self) -> None:
        tombstone = False

        tombstones = MagicMock()

        async def write_tombstone(*args: object, **kwargs: object) -> None:
            nonlocal tombstone
            del args, kwargs
            tombstone = True

        tombstones.save = AsyncMock(side_effect=write_tombstone)
        tombstones.exists = AsyncMock(side_effect=lambda *_: tombstone)
        persistence = MagicMock()
        persistence.adelete_thread = AsyncMock()
        deleting_worker = AgentManager(
            persistence, MagicMock(), tombstones, MagicMock(), MagicMock()
        )
        serving_worker = AgentManager(
            MagicMock(), MagicMock(), tombstones, MagicMock(), MagicMock()
        )

        await deleting_worker.delete_conversation_state(12, _CONVERSATION_ID)

        with self.assertRaisesRegex(RuntimeError, "已被删除"):
            await serving_worker.create_planner(12, _CONVERSATION_ID)
        persistence.adelete_thread.assert_awaited_once()
