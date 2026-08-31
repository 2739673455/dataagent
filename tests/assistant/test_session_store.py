"""专业 Agent Session Store 测试。"""

import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.agents.session_store import PostgresSandboxSessionStore


class SessionStoreArtifactTest(unittest.IsolatedAsyncioTestCase):
    def _store(self, backend: MagicMock) -> PostgresSandboxSessionStore:
        return PostgresSandboxSessionStore(
            user_id=7,
            conversation_id=uuid4(),
            persistence=MagicMock(),
            checkpointer=MagicMock(),
            sandbox=MagicMock(),
            conversation_backend=backend,
        )

    async def test_find_missing_files_uses_one_sandbox_command(self) -> None:
        backend = MagicMock()
        backend.aexecute = AsyncMock(
            return_value=MagicMock(
                exit_code=0,
                truncated=False,
                output="/sessions/sales/analyst/main/missing report.json\0",
            )
        )
        store = self._store(backend)
        paths = {
            "/sessions/sales/analyst/main/existing.json",
            "/sessions/sales/analyst/main/missing report.json",
        }

        missing = await store.find_missing_files(paths)

        self.assertEqual(
            missing,
            {"/sessions/sales/analyst/main/missing report.json"},
        )
        backend.aexecute.assert_awaited_once()

    async def test_find_missing_files_skips_empty_input(self) -> None:
        backend = MagicMock()
        backend.aexecute = AsyncMock()
        store = self._store(backend)

        missing = await store.find_missing_files(set())

        self.assertEqual(missing, set())
        backend.aexecute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
