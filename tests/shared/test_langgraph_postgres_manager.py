"""LangGraph PostgreSQL namespace 存储能力测试"""

from __future__ import annotations

import os
import unittest
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from app.shared.clients.langgraph_postgres_manager import LangGraphPostgresManager
from app.shared.config.app_config import cfg


class _Cursor:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _Transaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def execute(
        self,
        query: str,
        params: tuple[str, ...],
    ) -> _Cursor:
        self.calls.append((query, params))
        if "SELECT DISTINCT" in query:
            return _Cursor(
                rows=[
                    {"checkpoint_ns": "subagents/a/analyst/region"},
                    {"checkpoint_ns": "subagents/a/explorer/base"},
                ]
            )
        return _Cursor(rowcount=1 if "FROM checkpoints " in query else 0)

    def transaction(self) -> _Transaction:
        return _Transaction()


class _ConnectionContext(AbstractAsyncContextManager[_Connection]):
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class _Pool:
    def __init__(self) -> None:
        self.connection_value = _Connection()

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self.connection_value)


class LangGraphNamespaceStoreTest(unittest.IsolatedAsyncioTestCase):
    """验证 namespace 查询与删除严格限制在线程和 namespace 内"""

    def setUp(self) -> None:
        self.pool = _Pool()
        self.manager = LangGraphPostgresManager(MagicMock())
        cast(Any, self.manager)._pool = self.pool

    async def test_lists_unique_namespaces_by_thread_and_prefix(self) -> None:
        namespaces = await self.manager.list_checkpoint_namespaces(
            "thread",
            prefix="subagents/a/",
        )

        self.assertEqual(
            namespaces,
            ["subagents/a/analyst/region", "subagents/a/explorer/base"],
        )
        _, params = self.pool.connection_value.calls[0]
        self.assertEqual(params, ("thread", "subagents/a/", "subagents/a/"))

    async def test_deletes_all_tables_with_exact_identity(self) -> None:
        deleted = await self.manager.delete_checkpoint_namespace(
            "thread",
            "subagents/a/analyst/region",
        )

        self.assertTrue(deleted)
        self.assertEqual(len(self.pool.connection_value.calls), 3)
        for query, params in self.pool.connection_value.calls:
            self.assertIn("thread_id = %s AND checkpoint_ns = %s", query)
            self.assertEqual(params, ("thread", "subagents/a/analyst/region"))


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "requires PostgreSQL integration environment",
)
class LangGraphNamespaceStoreIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """使用真实 LangGraph 表验证 namespace 物理删除边界"""

    async def asyncSetUp(self) -> None:
        self.manager = LangGraphPostgresManager(cfg.langgraph_postgresql)
        await self.manager.init()
        self.thread_id = f"integration:session-lifecycle:{uuid4().hex}"

    async def asyncTearDown(self) -> None:
        await self.manager.delete_thread(self.thread_id)
        await self.manager.close()

    async def _put_checkpoint(self, namespace: str) -> RunnableConfig:
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"result": namespace}
        checkpoint["channel_versions"] = {"result": "1"}
        return await self.manager.get_checkpointer().aput(
            RunnableConfig(
                configurable={
                    "thread_id": self.thread_id,
                    "checkpoint_ns": namespace,
                }
            ),
            checkpoint,
            {},
            {"result": "1"},
        )

    async def test_delete_namespace_preserves_root_and_sibling(self) -> None:
        root_config = await self._put_checkpoint("")
        target_config = await self._put_checkpoint("subagents/analysis/analyst/region")
        sibling_config = await self._put_checkpoint("subagents/analysis/explorer/base")
        await self.manager.get_checkpointer().aput_writes(
            target_config,
            [("result", "pending")],
            task_id="integration-task",
        )

        namespaces = await self.manager.list_checkpoint_namespaces(
            self.thread_id,
            prefix="subagents/analysis/",
        )
        deleted = await self.manager.delete_checkpoint_namespace(
            self.thread_id,
            "subagents/analysis/analyst/region",
        )

        self.assertEqual(
            namespaces,
            [
                "subagents/analysis/analyst/region",
                "subagents/analysis/explorer/base",
            ],
        )
        self.assertTrue(deleted)
        self.assertIsNone(
            await self.manager.get_checkpointer().aget_tuple(target_config)
        )
        self.assertIsNotNone(
            await self.manager.get_checkpointer().aget_tuple(sibling_config)
        )
        self.assertIsNotNone(
            await self.manager.get_checkpointer().aget_tuple(root_config)
        )


if __name__ == "__main__":
    unittest.main()
