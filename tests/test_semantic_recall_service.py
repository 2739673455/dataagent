"""语义召回记录管理测试"""

import inspect
import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

from app.agents.explorer.semantic_recall_middleware import (
    SemanticRecallExpansionMiddleware,
)
from app.agents.explorer.semantic_recall_protocol import (
    semantic_recall_reference,
)
from app.agents.explorer.tools import (
    get_semantic_recall,
    list_semantic_recalls,
    search_semantic_resources,
)
from app.models.semantic_search import (
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
from app.services.authorization_service import AssetAccessPolicy, AssetIdentity
from app.services.metadata_authorization_filter import MetadataAuthorizationFilter
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


def build_authorization_filter(
    *grants: AssetIdentity,
    unrestricted: bool = False,
) -> MetadataAuthorizationFilter:
    """构造召回测试的资产授权过滤器"""
    return MetadataAuthorizationFilter(
        AssetAccessPolicy(
            user_id=7,
            grants=frozenset(grants),
            unrestricted=unrestricted,
        ),
        "doris",
        "analytics",
    )


class SemanticRecallServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证召回记录生命周期和会话隔离"""

    async def asyncSetUp(self) -> None:
        self.store = InMemoryStore()
        self.repo = SemanticRecallPGRepo(self.store)
        self.service = SemanticRecallService(
            self.repo,
            build_authorization_filter(unrestricted=True),
        )
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

    async def test_get_list_and_merge_apply_latest_policy_after_revocation(
        self,
    ) -> None:
        first = build_response("search_a", "本月收入", score=0.4, reason="a")
        first.warnings = ["backend failed while loading orders.amount"]
        second = build_response("search_b", "订单状态", score=0.8, reason="b")
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            SemanticSearchRequest(query="本月收入", resource_types=["column"]),
            first,
        )
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            SemanticSearchRequest(query="订单状态", resource_types=["value"]),
            second,
        )
        restricted = SemanticRecallService(
            self.repo,
            build_authorization_filter(
                AssetIdentity(
                    "doris",
                    "analytics",
                    "orders",
                    "status",
                )
            ),
        )

        recalled = await restricted.get(
            self.user_id,
            self.conversation_id,
            "search_a",
        )
        listed = await restricted.list(
            self.user_id,
            self.conversation_id,
            limit=10,
        )
        merged = await restricted.merge(
            self.user_id,
            self.conversation_id,
            ["search_a", "search_b"],
        )

        self.assertEqual(recalled.response.metrics, [])
        self.assertEqual(recalled.response.columns, [])
        self.assertEqual([item.value for item in recalled.response.values], ["paid"])
        self.assertEqual(recalled.response.tables[0].primary_key_columns, [])
        self.assertEqual(recalled.response.relations, [])
        self.assertEqual(recalled.response.warnings, [])
        self.assertTrue(all(record.response.columns == [] for record in listed))
        self.assertEqual(merged.response.metrics, [])
        self.assertEqual(merged.response.columns, [])
        self.assertEqual([item.value for item in merged.response.values], ["paid"])

        persisted = await self.repo.get(
            self.user_id,
            self.conversation_id,
            "search_a",
        )
        assert persisted is not None
        self.assertEqual([item.name for item in persisted.response.columns], ["amount"])
        self.assertEqual(
            persisted.response.warnings,
            ["backend failed while loading orders.amount"],
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

    async def test_tool_message_persists_reference_and_model_sees_authorized_record(
        self,
    ) -> None:
        store = InMemoryStore()
        repo = SemanticRecallPGRepo(store)
        unrestricted_service = SemanticRecallService(
            repo,
            build_authorization_filter(unrestricted=True),
        )
        conversation_id = uuid4()
        record = await unrestricted_service.record_search(
            7,
            conversation_id,
            SemanticSearchRequest(query="revenue", resource_types=["column"]),
            build_response("search_a", "revenue", score=0.8, reason="query"),
        )
        reference_content = json.dumps(
            semantic_recall_reference(record),
            ensure_ascii=False,
        )
        old_reference = ToolMessage(
            id="old_message",
            tool_call_id="old_call",
            name="search_semantic_resources",
            content=reference_content,
        )
        current_reference = ToolMessage(
            id="current_message",
            tool_call_id="current_call",
            name="search_semantic_resources",
            content=reference_content,
        )
        messages = [
            old_reference,
            HumanMessage(content="continue"),
            current_reference,
        ]
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=messages,
            runtime=Runtime(store=store),
        )
        restricted_service = SemanticRecallService(
            repo,
            build_authorization_filter(
                AssetIdentity("doris", "analytics", "orders", "status")
            ),
        )
        seen_messages: list[object] = []

        async def handler(model_request: ModelRequest[Any]) -> ModelResponse[Any]:
            seen_messages.extend(model_request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        with (
            patch(
                "app.agents.explorer.semantic_recall_middleware.get_config",
                return_value={
                    "configurable": {
                        "user_id": 7,
                        "conversation_id": str(conversation_id),
                    }
                },
            ),
            patch(
                "app.agents.explorer.semantic_recall_middleware."
                "create_authorized_semantic_recall_service",
                new=AsyncMock(return_value=restricted_service),
            ),
        ):
            await SemanticRecallExpansionMiddleware().awrap_model_call(
                request,
                handler,
            )

        self.assertEqual(current_reference.content, reference_content)
        self.assertNotIn("amount", reference_content)
        self.assertEqual(getattr(seen_messages[0], "content", None), reference_content)
        expanded_content = str(getattr(seen_messages[2], "content", ""))
        self.assertNotIn("amount", expanded_content)
        self.assertIn("paid", expanded_content)

    async def test_get_tool_writes_only_recall_reference_to_state(self) -> None:
        store = InMemoryStore()
        repo = SemanticRecallPGRepo(store)
        service = SemanticRecallService(
            repo,
            build_authorization_filter(unrestricted=True),
        )
        conversation_id = uuid4()
        await service.record_search(
            1,
            conversation_id,
            SemanticSearchRequest(query="revenue", resource_types=["column"]),
            build_response("search_a", "revenue", score=0.8, reason="query"),
        )
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([get_semantic_recall]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(store=store)

        with patch(
            "app.agents.explorer.tools.semantic_recall."
            "create_authorized_semantic_recall_service",
            new=AsyncMock(return_value=service),
        ):
            result = await graph.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "get_semantic_recall",
                                    "args": {"recall_id": "search_a"},
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

        content = str(result["messages"][-1].content)
        payload = json.loads(content)
        self.assertEqual(payload["recall_id"], "search_a")
        self.assertEqual(payload["status"], "stored")
        self.assertNotIn("semantic_recall", payload)
        self.assertNotIn("amount", content)

    async def test_tool_node_injects_store_and_conversation_context(self) -> None:
        store = InMemoryStore()
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([list_semantic_recalls]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(store=store)
        conversation_id = uuid4()

        service = SemanticRecallService(
            SemanticRecallPGRepo(store),
            build_authorization_filter(unrestricted=True),
        )
        with patch(
            "app.agents.explorer.tools.semantic_recall."
            "create_authorized_semantic_recall_service",
            new=AsyncMock(return_value=service),
        ):
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
