"""语义召回记录管理测试"""

import inspect
import json
import unittest
from typing import cast
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

from app.agent.tools import list_semantic_recalls, search_semantic_resources
from app.entities.semantic_search import (
    SemanticColumnResult,
    SemanticMatchReason,
    SemanticMetricResult,
    SemanticRelation,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticTableContext,
    SemanticValueResult,
)
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.repositories.value_es_repo import ValueESRepo
from app.services.chat_service import compact_semantic_recall_message
from app.services.semantic_recall_service import (
    SemanticRecallService,
    SemanticRecallsNotFoundError,
)


def build_response(
    search_id: str,
    query: str,
    *,
    score: float,
    reason: str,
) -> SemanticSearchResponse:
    """构造包含重复资源的测试召回响应"""
    match_reason = SemanticMatchReason(
        match_type="fulltext",
        query=reason,
        score=score,
    )
    return SemanticSearchResponse(
        status="success",
        search_id=search_id,
        queries=[query],
        metrics=[
            SemanticMetricResult(
                name="revenue",
                description="收入",
                alias=[f"收入-{reason}"],
                relevant_columns=[{"t_name": "orders", "c_name": "amount"}],
                rank_score=score,
                match_reasons=[match_reason],
                meta_version=1,
                index_version=1,
                index_status="current",
            )
        ],
        columns=[
            SemanticColumnResult(
                t_name="orders",
                name="amount",
                type="decimal",
                description="订单金额",
                alias=[f"金额-{reason}"],
                examples=[reason],
                reference_t_name=None,
                reference_c_name=None,
                inclusion_reasons=["direct_match"],
                rank_score=score,
                match_reasons=[match_reason],
                meta_version=1,
                index_version=1,
                index_status="current",
            )
        ],
        values=[
            SemanticValueResult(
                value="paid",
                t_name="orders",
                c_name="status",
                rank_score=score,
                match_reasons=[match_reason],
                sync_status="succeeded",
                synced_at=None,
            )
        ],
        tables=[
            SemanticTableContext(
                name="orders",
                role="fact",
                description="订单事实表",
                primary_key_columns=["id"],
                meta_version=1,
            )
        ],
        relations=[
            SemanticRelation(
                source_t_name="orders",
                source_c_name="customer_id",
                target_t_name="customers",
                target_c_name="id",
            )
        ],
        warnings=[],
        truncated=False,
    )


class SemanticRecallServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证召回记录生命周期和会话隔离"""

    async def asyncSetUp(self) -> None:
        self.store = InMemoryStore()
        self.repo = SemanticRecallPGRepo(self.store)
        self.service = SemanticRecallService(self.repo)
        self.user_id = 7
        self.conversation_id = uuid4()

    async def _record(
        self,
        search_id: str,
        query: str,
        score: float,
        reason: str,
    ) -> None:
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            SemanticSearchRequest(query=query, resource_types=["column"]),
            build_response(search_id, query, score=score, reason=reason),
        )

    async def test_each_search_is_persisted_with_request_and_result(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")
        await self._record("search_b", "订单金额", 0.8, "query_b")

        records = await self.service.list(
            self.user_id,
            self.conversation_id,
            limit=10,
        )

        self.assertEqual(
            {record.recall_id for record in records}, {"search_a", "search_b"}
        )
        by_id = {record.recall_id: record for record in records}
        search_a_request = by_id["search_a"].request
        assert search_a_request is not None
        self.assertEqual(search_a_request.query, "本月收入")
        self.assertEqual(by_id["search_b"].response.queries, ["订单金额"])
        self.assertIsNone(await self.repo.get(8, self.conversation_id, "search_a"))
        self.assertIsNone(await self.repo.get(self.user_id, uuid4(), "search_a"))

    async def test_merge_creates_deduplicated_snapshot_and_keeps_sources(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")
        await self._record("search_b", "订单金额", 0.8, "query_b")

        merged = await self.service.merge(
            self.user_id,
            self.conversation_id,
            ["search_a", "search_b"],
        )

        self.assertEqual(merged.kind, "merged")
        self.assertEqual(merged.source_recall_ids, ["search_a", "search_b"])
        self.assertEqual(merged.response.queries, ["本月收入", "订单金额"])
        self.assertEqual(len(merged.response.metrics), 1)
        self.assertEqual(merged.response.metrics[0].rank_score, 0.8)
        self.assertEqual(
            [reason.query for reason in merged.response.metrics[0].match_reasons],
            ["query_a", "query_b"],
        )
        self.assertEqual(merged.response.columns[0].examples, ["query_a", "query_b"])
        self.assertIsNotNone(
            await self.repo.get(self.user_id, self.conversation_id, "search_a")
        )
        self.assertIsNotNone(
            await self.repo.get(self.user_id, self.conversation_id, "search_b")
        )

    async def test_delete_reports_missing_records(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")

        deleted, missing = await self.service.delete(
            self.user_id,
            self.conversation_id,
            ["search_a", "unknown", "search_a"],
        )

        self.assertEqual(deleted, ["search_a"])
        self.assertEqual(missing, ["unknown"])
        self.assertIsNone(
            await self.repo.get(self.user_id, self.conversation_id, "search_a")
        )

    async def test_merge_rejects_missing_record(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")

        with self.assertRaises(SemanticRecallsNotFoundError) as context:
            await self.service.merge(
                self.user_id,
                self.conversation_id,
                ["search_a", "unknown"],
            )

        self.assertEqual(context.exception.recall_ids, ["unknown"])

    async def test_delete_all_removes_only_target_conversation(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")
        other_conversation_id = uuid4()
        await self.service.record_search(
            self.user_id,
            other_conversation_id,
            SemanticSearchRequest(query="其他查询", resource_types=["column"]),
            build_response(
                "search_other",
                "其他查询",
                score=0.5,
                reason="other",
            ),
        )

        await self.repo.delete_all(self.user_id, self.conversation_id)

        self.assertEqual(
            await self.service.list(
                self.user_id,
                self.conversation_id,
                limit=10,
            ),
            [],
        )
        self.assertIsNotNone(
            await self.repo.get(
                self.user_id,
                other_conversation_id,
                "search_other",
            )
        )


class SemanticRecallToolTest(unittest.IsolatedAsyncioTestCase):
    """验证模型可见参数和工具运行时注入"""

    def test_runtime_is_hidden_from_tool_call_schema(self) -> None:
        for semantic_tool in (search_semantic_resources, list_semantic_recalls):
            schema = cast(type[BaseModel], semantic_tool.tool_call_schema)
            properties = schema.model_json_schema().get(
                "properties",
                {},
            )
            self.assertNotIn("runtime", properties)
        search_schema = cast(
            type[BaseModel],
            search_semantic_resources.tool_call_schema,
        ).model_json_schema()
        search_properties = search_schema["properties"]
        self.assertNotIn("table_names", search_properties)
        self.assertNotIn("include_relations", search_properties)
        self.assertIn("resource_types", search_schema["required"])

    def test_full_tool_result_is_compacted_to_recall_reference(self) -> None:
        message = ToolMessage(
            id="message_1",
            tool_call_id="call_1",
            name="search_semantic_resources",
            content=(
                '{"status":"success","recall_id":"search_a",'
                '"metrics":[{"name":"revenue"}]}'
            ),
        )

        compact = compact_semantic_recall_message(message)

        assert compact is not None
        self.assertEqual(compact.id, "message_1")
        self.assertEqual(compact.tool_call_id, "call_1")
        self.assertNotIn("revenue", str(compact.content))
        self.assertEqual(
            json.loads(str(compact.content))["recall_ids"],
            ["search_a"],
        )

    async def test_tool_node_injects_store_and_conversation_context(self) -> None:
        store = InMemoryStore()
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([list_semantic_recalls]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(store=store)
        conversation_id = uuid4()

        result = await graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_semantic_recalls",
                                "args": {"limit": 20},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            },
            {
                "configurable": {
                    "user_id": 1,
                    "conversation_id": str(conversation_id),
                }
            },
        )

        self.assertEqual(
            result["messages"][-1].content,
            '{"status": "success", "recalls": []}',
        )


class SemanticSearchContractTest(unittest.TestCase):
    """验证语义检索不再暴露表范围能力"""

    def test_table_scope_is_absent_from_all_search_layers(self) -> None:
        self.assertNotIn("table_names", SemanticSearchRequest.model_fields)
        for method in (
            ColumnESRepo.search_text_hits,
            ColumnESRepo.search_vector_hits,
            ValueESRepo.search_hits,
        ):
            self.assertNotIn("table_names", inspect.signature(method).parameters)


if __name__ == "__main__":
    unittest.main()
