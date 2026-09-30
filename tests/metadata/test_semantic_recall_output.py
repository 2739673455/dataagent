"""召回全量、增量和固定工具消息的回归测试。"""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.assistant.agents.tools.semantic_recall import (
    create_semantic_recall_tools,
    semantic_recall_update,
)
from app.metadata.models.recall import SemanticRecallResourceDeletion
from app.metadata.models.search import SemanticRecallFailure
from app.metadata.services.recall import SemanticRecallContextService
from app.metadata.services.recall_application import SemanticRecallService
from tests.metadata.test_semantic_recall_service import (
    _FULL_DATABASE_GRANT,
    InMemorySemanticRecallRepo,
    build_authorization_filter,
    build_query_experience,
    build_request,
    build_response,
    recall_repo,
)


class SemanticRecallOutputTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = InMemorySemanticRecallRepo()
        self.service = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        self.conversation_id = uuid4()
        self.request = build_request("收入", ["column", "metric", "value"])
        self.experience = build_query_experience()

    def response(self, name="amount", value="paid", version=1):
        response = build_response(uuid4().hex, "收入", score=0.8, reason="收入")
        response.columns[0].name = name
        response.columns[0].meta_version = version
        response.values[0].c_name = name
        response.values[0].value = value
        return response

    async def record(self, response, *, query="收入", experiences=None):
        return await self.service.record(
            7,
            self.conversation_id,
            query,
            self.request,
            response,
            [self.experience] if experiences is None else experiences,
            datetime.now(UTC),
        )

    async def test_first_full_then_duplicates_have_counts_but_no_repeated_details(self):
        first = semantic_recall_update(await self.record(self.response()))
        second = semantic_recall_update(await self.record(self.response()))
        self.assertEqual(first["mode"], "full")
        self.assertEqual(
            first["tables"]["orders"]["columns"]["amount"]["values"], ["paid"]
        )
        self.assertEqual(second["mode"], "delta")
        self.assertEqual(second["tables"], {})
        self.assertEqual(second["metrics"], {})
        self.assertEqual(second["query_experiences"], [])
        self.assertEqual(
            second["recalled_counts"],
            {
                "tables": 1,
                "columns": 1,
                "values": 1,
                "metrics": 1,
                "query_experiences": 1,
            },
        )
        other = semantic_recall_update(
            await self.record(self.response(), query="另一个问题")
        )
        self.assertEqual(other["mode"], "full")

    async def test_added_column_and_value_are_incremental_and_counts_are_not_cumulative(
        self,
    ):
        await self.record(self.response())
        value_delta = semantic_recall_update(
            await self.record(self.response(value="refunded"))
        )
        self.assertEqual(
            value_delta["tables"],
            {
                "orders": {"columns": {"amount": {"values": ["refunded"]}}},
            },
        )
        update = await self.record(self.response(name="quantity", value="1"))
        delta = semantic_recall_update(update)
        self.assertEqual(set(delta["tables"]["orders"]["columns"]), {"quantity"})
        self.assertEqual(delta["recalled_counts"]["columns"], 1)
        self.assertEqual(delta["recalled_counts"]["values"], 1)
        self.assertEqual(len(update.record.response.columns), 2)
        self.assertEqual(len(update.record.response.values), 3)

    async def test_metadata_changes_are_returned_without_ranking_noise(self):
        await self.record(self.response())
        response = self.response(version=2)
        response.columns[0].description = "更新后的口径"
        response.metrics[0].meta_version = 2
        response.metrics[0].description = "更新后的指标"
        delta = semantic_recall_update(await self.record(response))
        self.assertEqual(
            delta["tables"],
            {
                "orders": {"columns": {"amount": {"description": "更新后的口径"}}},
            },
        )
        self.assertEqual(delta["metrics"]["revenue"]["description"], "更新后的指标")
        ranked = response.model_copy(deep=True)
        ranked.columns[0].rank_score = 0.99
        ranked.metrics[0].rank_score = 0.99
        unchanged = semantic_recall_update(await self.record(ranked))
        self.assertEqual(unchanged["tables"], {})
        self.assertEqual(unchanged["metrics"], {})

    async def test_partial_and_empty_hits_keep_actual_counts_and_diagnostics(self):
        await self.record(self.response())
        response = self.response()
        response.tables = []
        response.columns = []
        response.metrics = []
        response.values = []
        response.status = "partial"
        response.failures = [
            SemanticRecallFailure(resource_type="column", channel="vector", term="收入")
        ]
        response.warnings = ["向量检索失败"]
        response.truncated = True
        delta = semantic_recall_update(await self.record(response, experiences=[]))
        self.assertEqual(delta["status"], "partial")
        self.assertEqual(
            delta["recalled_counts"],
            dict.fromkeys(
                ["tables", "columns", "values", "metrics", "query_experiences"], 0
            ),
        )
        self.assertEqual(delta["tables"], {})
        self.assertEqual(delta["warnings"], ["向量检索失败"])
        self.assertEqual(delta["failures"][0]["channel"], "vector")
        self.assertTrue(delta["truncated"])
        self.assertEqual(
            delta["removed"]["query_experiences"], [str(self.experience.id)]
        )

    async def test_concurrent_updates_compare_against_the_locked_latest_snapshot(self):
        lock = asyncio.Lock()
        self.repo.acquire_query_lock = AsyncMock(side_effect=lambda *args: None)

        async def acquire(*args):
            await lock.acquire()
            await asyncio.sleep(0)

        self.repo.acquire_query_lock.side_effect = acquire
        recall = SemanticRecallService(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )

        @asynccontextmanager
        async def context(*args, **kwargs):
            try:
                yield self.service
            finally:
                lock.release()

        with (
            patch.object(
                recall,
                "search",
                AsyncMock(
                    side_effect=[
                        (MagicMock(), self.response()),
                        (MagicMock(), self.response()),
                    ]
                ),
            ),
            patch.object(
                recall,
                "query_experiences",
                AsyncMock(return_value=([], datetime.now(UTC))),
            ),
            patch.object(recall, "context_service", context),
        ):
            updates = await asyncio.gather(
                *[
                    recall.recall_context(7, self.conversation_id, "收入", self.request)
                    for _ in range(2)
                ]
            )
        payloads = [semantic_recall_update(update) for update in updates]
        self.assertEqual([item["mode"] for item in payloads], ["full", "delta"])
        self.assertEqual(payloads[1]["tables"], {})
        self.assertEqual(self.repo.acquire_query_lock.await_count, 2)

    async def test_checkpoint_results_stay_fixed_after_new_recall_and_deletion(self):
        recall = SemanticRecallService(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )

        @asynccontextmanager
        async def context(*args, **kwargs):
            yield self.service

        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode(create_semantic_recall_tools(recall)))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        saver = InMemorySaver()
        graph = builder.compile(checkpointer=saver)
        config: RunnableConfig = {
            "configurable": {
                "thread_id": "fixed-recall",
                "user_id": 7,
                "conversation_id": str(self.conversation_id),
            }
        }

        def request(call_id: str) -> MessagesState:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": call_id,
                                "name": "recall_context",
                                "args": {
                                    "query": "收入",
                                    "terms": ["金额"],
                                    "resource_types": ["column"],
                                },
                            }
                        ],
                    )
                ]
            }

        with (
            patch.object(
                recall,
                "search",
                AsyncMock(
                    side_effect=[
                        (MagicMock(), self.response()),
                        (MagicMock(), self.response(name="quantity")),
                    ]
                ),
            ),
            patch.object(
                recall,
                "query_experiences",
                AsyncMock(return_value=([], datetime.now(UTC))),
            ),
            patch.object(recall, "context_service", context),
        ):
            first = await graph.ainvoke(request("first"), config)
            original = first["messages"][-1].content
            await graph.ainvoke(request("second"), config)
        await self.service.delete(
            7, self.conversation_id, [SemanticRecallResourceDeletion(query="收入")]
        )
        restored = builder.compile(checkpointer=saver)
        state = await restored.aget_state(config)
        results = [
            message
            for message in state.values["messages"]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(results[0].content, original)
        self.assertEqual(json.loads(str(results[0].content))["mode"], "full")
        self.assertEqual(json.loads(str(results[1].content))["mode"], "delta")
        self.assertNotIn(
            "quantity",
            json.loads(str(results[0].content))["tables"]["orders"]["columns"],
        )
