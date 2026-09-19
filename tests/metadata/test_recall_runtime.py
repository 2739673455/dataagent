"""验证召回操作的授权刷新和数据库会话边界。"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.models.search import SemanticResourceRecallRequest
from app.shared.config.app_config import cfg


class RecallRuntimeTest(unittest.IsolatedAsyncioTestCase):
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

        self.runtime = SemanticRecallRuntime(
            MagicMock(session=lambda: session("auth")),
            MagicMock(session=lambda: session("meta")),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        self.policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {AssetIdentity(cfg.query.data_source, cfg.doris.database)}
            ),
        )
        self.request = SemanticResourceRecallRequest(
            terms=["收入"], resource_types=["column"]
        )
        self.table = TableInfo(
            name="orders",
            role="fact",
            description="订单",
            primary_key_columns=[],
            meta_version=1,
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
            meta_version=1,
            index_version=1,
        )

    async def test_search_closes_auth_and_catalog_sessions_before_external_calls(self):
        async def load_policy(user_id):
            self.assertEqual(self.open_sessions, {"auth"})
            return self.policy

        async def columns():
            self.assertEqual(self.open_sessions, {"meta"})
            return [self.column]

        async def hits(*args, **kwargs):
            self.assertEqual(self.open_sessions, set())
            self.assertEqual(
                kwargs["allowed_columns"], frozenset({("orders", "amount")})
            )
            return []

        async def embed(terms):
            self.assertEqual(self.open_sessions, set())
            self.assertEqual(terms, ["收入"])
            return [[0.1, 0.2]]

        index = MagicMock(
            search_text_hits=AsyncMock(side_effect=hits),
            search_vector_hits=AsyncMock(side_effect=hits),
        )
        embedding = MagicMock(aembed_documents=AsyncMock(side_effect=embed))
        with (
            patch.object(self.runtime.embedding, "get_client", return_value=embedding),
            patch(
                "app.identity.providers.AuthorizationService",
                return_value=MagicMock(
                    get_asset_policy=AsyncMock(side_effect=load_policy)
                ),
            ),
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
            policy, response = await self.runtime.search(7, self.request)
        self.assertIs(policy, self.policy)
        self.assertEqual(response.status, "success")
        index.search_text_hits.assert_awaited_once()
        index.search_vector_hits.assert_awaited_once()
        embedding.aembed_documents.assert_awaited_once()
        self.assertEqual(self.closed, ["auth", "meta"])

    async def test_cancel_during_catalog_loading_closes_read_session(self):
        entered = asyncio.Event()

        async def columns():
            entered.set()
            await asyncio.Event().wait()

        with (
            patch(
                "app.assistant.agents.explorer.recall_runtime.load_asset_policy",
                new=AsyncMock(return_value=self.policy),
            ),
            patch(
                "app.metadata.providers.MetaPGRepo",
                return_value=MagicMock(
                    list_table_infos=AsyncMock(return_value=[self.table]),
                    list_column_infos=AsyncMock(side_effect=columns),
                ),
            ),
        ):
            async with asyncio.timeout(1):
                task = asyncio.create_task(self.runtime.search(7, self.request))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(self.open_sessions, set())
        self.assertEqual(self.closed, ["meta"])

    async def test_independent_context_reads_refresh_policy_but_same_operation_reuses_it(
        self,
    ):
        revoked = AssetAccessPolicy(user_id=7)
        seen = []

        @asynccontextmanager
        async def context(postgres, policy):
            self.assertEqual(self.open_sessions, set())
            seen.append(policy)
            yield MagicMock()

        with (
            patch(
                "app.assistant.agents.explorer.recall_runtime.load_asset_policy",
                new=AsyncMock(side_effect=[self.policy, revoked]),
            ) as load,
            patch(
                "app.assistant.agents.explorer.recall_runtime.semantic_recall_context",
                side_effect=context,
            ),
        ):
            async with self.runtime.context_service(7):
                pass
            async with self.runtime.context_service(7, policy=self.policy):
                pass
            async with self.runtime.context_service(7):
                pass
        self.assertEqual(load.await_count, 2)
        self.assertEqual(seen, [self.policy, self.policy, revoked])

    async def test_experience_backend_failure_keeps_stale_timestamp_for_retry(self):
        policy = AssetAccessPolicy(
            user_id=7, role_name="analyst", authorization_fingerprint="a" * 64
        )

        @asynccontextmanager
        async def context(*args):
            yield MagicMock(get_fresh_query_experiences=AsyncMock(return_value=None))

        with (
            patch(
                "app.assistant.agents.explorer.recall_runtime.semantic_recall_context",
                side_effect=context,
            ),
            patch(
                "app.assistant.agents.explorer.recall_runtime.build_query_experience_recall_service",
                return_value=MagicMock(
                    recall=AsyncMock(return_value=SimpleNamespace(status="failed"))
                ),
            ),
        ):
            results, retrieved = await self.runtime.query_experiences(
                7, uuid4(), "收入", policy
            )
        self.assertEqual(results, [])
        self.assertEqual(retrieved.year, 1)
        self.assertEqual(self.open_sessions, set())
