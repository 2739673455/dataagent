"""专业 Agent Session Store 测试"""

import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.analytics.agents.session_store import PostgresSandboxSessionStore


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
                output="/analyses/sales/missing report.json\0",
            )
        )
        store = self._store(backend)
        paths = {
            "/analyses/sales/existing.json",
            "/analyses/sales/missing report.json",
        }

        missing = await store.find_missing_files(paths)

        self.assertEqual(missing, {"/analyses/sales/missing report.json"})
        backend.aexecute.assert_awaited_once()
        self.assertEqual(backend.aexecute.await_args.kwargs, {"timeout": 10})

    async def test_find_missing_files_skips_empty_input(self) -> None:
        backend = MagicMock()
        backend.aexecute = AsyncMock()
        store = self._store(backend)

        missing = await store.find_missing_files(set())

        self.assertEqual(missing, set())
        backend.aexecute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
