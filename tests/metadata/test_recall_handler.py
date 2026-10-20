"""验证完整目录召回的数据库会话边界。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.models.search import SemanticResourceRecallRequest
from app.metadata.services.recall_handler import SemanticRecallHandler


class RecallHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.open_sessions = set()
        self.closed = []

        @asynccontextmanager
        async def async_transaction():
            yield

        @asynccontextmanager
        async def session(name):
            self.open_sessions.add(name)
            try:
                yield MagicMock(begin=lambda: async_transaction())
            finally:
                self.open_sessions.remove(name)
                self.closed.append(name)

        self.handler = SemanticRecallHandler(
            MagicMock(session=lambda: session("meta")),
            MagicMock(),
            MagicMock(),
        )
        self.request = SemanticResourceRecallRequest(
            terms=["收入"], resource_types=["column"]
        )
        self.table = TableInfo(
            name="orders",
            role="fact",
            description="订单",
            primary_key_columns=[],
        )
        self.column = ColumnInfo(
            t_name="orders",
            name="amount",
            type="DECIMAL",
            description="金额",
            alias=[],
            examples=[],
            reference_t_name=None,
            reference_c_name=None,
            index_values=False,
        )

    async def test_search_closes_catalog_session_before_external_calls(self):
        async def columns():
            self.assertEqual(self.open_sessions, {"meta"})
            return [self.column]

        async def hits(*args, **kwargs):
            self.assertEqual(self.open_sessions, set())
            self.assertEqual(kwargs["catalog_keys"], frozenset({("orders", "amount")}))
            return []

        async def embed(terms):
            self.assertEqual(self.open_sessions, set())
            self.assertEqual(terms, ["收入"])
            return [[0.1, 0.2]]

        index = MagicMock(
            search_text_hits=AsyncMock(side_effect=hits),
            search_vector_hits=AsyncMock(side_effect=hits),
        )
        with (
            patch.object(
                self.handler.embedding,
                "aembed_documents",
                new=AsyncMock(side_effect=embed),
            ) as embed_client,
            patch(
                "app.metadata.providers.MetaPGRepo",
                return_value=MagicMock(
                    list_table_infos=AsyncMock(return_value=[self.table]),
                    list_column_infos=AsyncMock(side_effect=columns),
                    list_metric_infos=AsyncMock(return_value=[]),
                ),
            ),
            patch("app.metadata.providers.ColumnESRepo", return_value=index),
        ):
            response = await self.handler.search(self.request)
        self.assertEqual(response.status, "success")
        index.search_text_hits.assert_awaited_once()
        index.search_vector_hits.assert_awaited_once()
        embed_client.assert_awaited_once()
        self.assertEqual(self.closed, ["meta"])

    async def test_cancel_during_catalog_loading_closes_read_session(self):
        entered = asyncio.Event()

        async def columns():
            entered.set()
            await asyncio.Event().wait()

        with (
            patch(
                "app.metadata.providers.MetaPGRepo",
                return_value=MagicMock(
                    list_table_infos=AsyncMock(return_value=[self.table]),
                    list_column_infos=AsyncMock(side_effect=columns),
                ),
            ),
        ):
            async with asyncio.timeout(1):
                task = asyncio.create_task(self.handler.search(self.request))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(self.open_sessions, set())
        self.assertEqual(self.closed, ["meta"])
