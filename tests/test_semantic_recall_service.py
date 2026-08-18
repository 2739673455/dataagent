"""语义召回记录管理测试"""

import inspect
import json
import unittest
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

from app.agents.contracts import PlannerTurnContext
from app.agents.data_query.tools import (
    list_semantic_recalls,
    search_semantic_resources,
)
from app.agents.session_service import AgentSessionService
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
from app.routes.api.v1.auth.dependencies import (
    get_authorization_service,
    get_current_user,
)
from app.routes.api.v1.chat.dependencies import (
    get_conversation_pg_repo,
    get_semantic_recall_pg_repo,
)
from app.routes.api.v1.chat.router import router as chat_router
from app.services.authorization_service import AssetAccessPolicy, AssetIdentity
from app.services.chat_service import list_messages
from app.services.checkpoint_recall_sanitizer import compact_semantic_recall_message
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


class PlannerExecutionStub:
    """测试用 Planner 执行上下文"""

    async def __aenter__(self) -> PlannerTurnContext:
        return PlannerTurnContext(
            user_id=7,
            conversation_id=uuid4(),
            planner_run_id="planner-run",
            max_continuations=3,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class SpecialistCheckpointStub:
    """记录专业 Agent 调用前后检查点消息的测试图"""

    def __init__(self, messages: list[ToolMessage]) -> None:
        self.messages = messages
        self.seen_at_invoke: list[str] = []

    async def aget_state(self, config: RunnableConfig) -> MagicMock:
        return MagicMock(values={"messages": self.messages})

    async def aupdate_state(
        self,
        config: RunnableConfig,
        values: dict[str, object],
    ) -> None:
        replacements = values["messages"]
        assert isinstance(replacements, list)
        by_id = {
            message.id: message
            for message in replacements
            if isinstance(message, ToolMessage)
        }
        self.messages = [by_id.get(message.id, message) for message in self.messages]

    async def ainvoke(
        self,
        input_state: object,
        *,
        config: RunnableConfig,
    ) -> dict[str, str]:
        self.seen_at_invoke = [str(message.content) for message in self.messages]
        self.messages.append(
            ToolMessage(
                id="message_2",
                tool_call_id="call_2",
                name="search_semantic_resources",
                content=(
                    '{"status":"success","recall_id":"search_b",'
                    '"columns":[{"name":"current_secret"}]}'
                ),
            )
        )
        return {"status": "completed"}


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

    async def test_checkpoint_read_compacts_full_payload_left_by_crash(self) -> None:
        conversation_id = uuid4()
        full_message = ToolMessage(
            id="message_1",
            tool_call_id="call_1",
            name="get_semantic_recall",
            content=(
                '{"status":"success","recall":{"recall_id":"search_a",'
                '"response":{"columns":[{"name":"revoked_secret"}]}}}'
            ),
        )
        compact = compact_semantic_recall_message(full_message)
        assert compact is not None
        raw_state = MagicMock(values={"messages": [full_message]})
        compact_state = MagicMock(values={"messages": [compact]})
        planner = MagicMock()
        planner.aget_state = AsyncMock(side_effect=[raw_state, compact_state])
        planner.aupdate_state = AsyncMock()
        bundle = MagicMock(planner=planner)

        with (
            patch(
                "app.services.chat_service.agent_manager.get_agent_bundle",
                new=AsyncMock(return_value=bundle),
            ),
            patch(
                "app.services.chat_service.agent_manager.execution",
                return_value=PlannerExecutionStub(),
            ),
        ):
            await list_messages(7, conversation_id)

        update = planner.aupdate_state.await_args.args[1]
        stored_content = str(update["messages"][0].content)
        self.assertNotIn("revoked_secret", stored_content)
        self.assertIn("search_a", stored_content)

    async def test_specialist_resume_compacts_crash_payload_before_and_after_run(
        self,
    ) -> None:
        specialist = SpecialistCheckpointStub(
            [
                ToolMessage(
                    id="message_1",
                    tool_call_id="call_1",
                    name="get_semantic_recall",
                    content=(
                        '{"status":"success","recall":{"recall_id":"search_a",'
                        '"response":{"columns":[{"name":"revoked_secret"}]}}}'
                    ),
                )
            ]
        )

        await AgentSessionService._invoke_with_sanitized_checkpoint(
            cast(CompiledStateGraph, specialist),
            {"messages": [HumanMessage(content="resume")]},
            RunnableConfig(configurable={"thread_id": "test"}),
        )

        self.assertTrue(
            all(
                "revoked_secret" not in content for content in specialist.seen_at_invoke
            )
        )
        persisted = [str(message.content) for message in specialist.messages]
        self.assertTrue(all("revoked_secret" not in content for content in persisted))
        self.assertTrue(all("current_secret" not in content for content in persisted))
        self.assertTrue(any("search_a" in content for content in persisted))
        self.assertTrue(any("search_b" in content for content in persisted))

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
            "app.agents.data_query.tools.semantic_recall."
            "_get_authorized_semantic_recall_context",
            new=AsyncMock(return_value=(1, conversation_id, service)),
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


class SemanticRecallRouterAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    """验证召回 REST 每次请求重新加载当前资产策略"""

    async def test_list_route_applies_revocation_to_persisted_snapshot(self) -> None:
        store = InMemoryStore()
        repo = SemanticRecallPGRepo(store)
        conversation_id = uuid4()
        unrestricted = AssetAccessPolicy(user_id=7, unrestricted=True)
        restricted = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        "doris",
                        "analytics",
                        "orders",
                        "status",
                    )
                }
            ),
        )
        service = SemanticRecallService(
            repo,
            MetadataAuthorizationFilter(unrestricted, "doris", "analytics"),
        )
        response = build_response("search_a", "收入", score=0.8, reason="query")
        response.warnings = ["orders.amount backend detail"]
        await service.record_search(
            7,
            conversation_id,
            SemanticSearchRequest(query="收入", resource_types=["column"]),
            response,
        )

        conversation_repo = MagicMock()
        conversation_repo.get = AsyncMock(return_value=object())
        authorization_service = MagicMock()
        authorization_service.get_asset_policy = AsyncMock(
            side_effect=[unrestricted, restricted]
        )
        current_user = MagicMock(id=7)
        app = FastAPI()
        app.include_router(chat_router, prefix="/api/v1/chat")

        async def override_conversation_repo():
            return conversation_repo

        async def override_recall_repo():
            return repo

        async def override_current_user():
            return current_user

        async def override_authorization_service():
            return authorization_service

        app.dependency_overrides[get_conversation_pg_repo] = override_conversation_repo
        app.dependency_overrides[get_semantic_recall_pg_repo] = override_recall_repo
        app.dependency_overrides[get_current_user] = override_current_user
        app.dependency_overrides[get_authorization_service] = (
            override_authorization_service
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            before = await client.get(f"/api/v1/chat/recalls/{conversation_id}")
            after = await client.get(f"/api/v1/chat/recalls/{conversation_id}")

        self.assertEqual(before.status_code, 200)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(
            [
                item["name"]
                for item in before.json()["recalls"][0]["response"]["columns"]
            ],
            ["amount"],
        )
        filtered_response = after.json()["recalls"][0]["response"]
        self.assertEqual(filtered_response["columns"], [])
        self.assertEqual(filtered_response["metrics"], [])
        self.assertEqual(filtered_response["warnings"], [])
        self.assertEqual(authorization_service.get_asset_policy.await_count, 2)


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
