"""语义召回记录管理测试"""

import inspect
import json
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError

from app.analytics.agents.explorer.semantic_recall_middleware import (
    SemanticRecallExpansionMiddleware,
    _expanded_content,
)
from app.analytics.agents.explorer.semantic_recall_protocol import (
    parse_semantic_recall_reference,
    semantic_recall_reference,
)
from app.analytics.agents.explorer.tools import (
    delete_recalls,
    get_recall,
    list_recalls,
    merge_recalls,
    search_context,
)
from app.analytics.agents.explorer.tools.semantic_recall import _record_summary
from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.recall import SemanticRecallRecord
from app.metadata.models.search import (
    SemanticColumnResult,
    SemanticMatchReason,
    SemanticMetricResult,
    SemanticRelation,
    SemanticResourceSearchRequest,
    SemanticResourceType,
    SemanticSearchResponse,
    SemanticTableContext,
    SemanticValueResult,
)
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticQueriesNotFoundError,
    SemanticRecallService,
)
from app.shared.contracts.query_experience import (
    QueryAssetSnapshot,
    QueryExperienceSearchResult,
)

_FULL_DATABASE_GRANT = AssetIdentity("doris", "analytics")
_CONFIGURED_DATABASE_GRANT = AssetIdentity("doris", "ecommerce")


class InMemorySemanticRecallRepo:
    """为服务与工具单元测试提供进程内召回仓储"""

    def __init__(self) -> None:
        """初始化空召回记录集合"""
        self.records: dict[tuple[int, object, str], SemanticRecallRecord] = {}

    async def save(self, record: SemanticRecallRecord) -> None:
        """保存召回记录"""
        self.records[
            (record.user_id, record.conversation_id, record.response.search_id)
        ] = record

    async def get_latest_by_query(
        self,
        user_id: int,
        conversation_id: object,
        query: str,
    ) -> SemanticRecallRecord | None:
        """获取指定 query 的最新召回记录"""
        records = [
            record
            for (owner_id, owner_conversation_id, _), record in self.records.items()
            if owner_id == user_id
            and owner_conversation_id == conversation_id
            and record.query == query
        ]
        return max(
            records,
            key=lambda record: (record.created_at, record.response.search_id),
            default=None,
        )

    async def list(
        self,
        user_id: int,
        conversation_id: object,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[SemanticRecallRecord]:
        """按创建时间倒序列出每个 query 的最新召回记录"""
        latest_by_query: dict[str, SemanticRecallRecord] = {}
        for (owner_id, owner_conversation_id, _), record in self.records.items():
            if owner_id != user_id or owner_conversation_id != conversation_id:
                continue
            current = latest_by_query.get(record.query)
            if current is None or (record.created_at, record.response.search_id) > (
                current.created_at,
                current.response.search_id,
            ):
                latest_by_query[record.query] = record
        records = sorted(
            latest_by_query.values(),
            key=lambda record: (record.created_at, record.response.search_id),
            reverse=True,
        )
        return records[offset : offset + limit]

    async def delete_by_query(
        self,
        user_id: int,
        conversation_id: object,
        query: str,
    ) -> bool:
        """删除 query 的全部召回记录"""
        keys = [
            key
            for key, record in self.records.items()
            if key[:2] == (user_id, conversation_id) and record.query == query
        ]
        for key in keys:
            del self.records[key]
        return bool(keys)

    async def delete_all(self, user_id: int, conversation_id: object) -> None:
        """删除会话全部召回记录"""
        self.records = {
            key: value
            for key, value in self.records.items()
            if key[:2] != (user_id, conversation_id)
        }


def recall_repo(repo: InMemorySemanticRecallRepo) -> SemanticRecallPGRepo:
    """将测试仓储收窄为服务声明的具体仓储类型"""
    return cast(SemanticRecallPGRepo, repo)


@asynccontextmanager
async def recall_repository_context(
    repo: InMemorySemanticRecallRepo,
) -> AsyncGenerator[SemanticRecallPGRepo]:
    """模拟工具使用的短事务召回仓储上下文"""
    yield recall_repo(repo)


@asynccontextmanager
async def object_context(value: Any) -> AsyncGenerator[Any, None]:
    """为工具依赖提供简单异步上下文"""
    yield value


def build_query_experience(
    *,
    table: str = "orders",
    column: str = "amount",
) -> QueryExperienceSearchResult:
    """构造紧凑查询经验结果"""
    return QueryExperienceSearchResult(
        experience_id=uuid4(),
        purpose="查询订单收入",
        sql_template=f"SELECT {column} FROM {table}",
        dialect="doris",
        assets=[
            QueryAssetSnapshot(
                kind="column",
                database="analytics",
                table=table,
                column=column,
                meta_version=1,
            )
        ],
        quality="promoted",
        success_count=3,
        adopted_count=1,
        score=0.9,
        match_reasons=["vector_match", "column_overlap"],
        last_used_at=datetime.now(UTC),
    )


def build_request(
    query: str,
    resource_types: list[SemanticResourceType],
) -> SemanticResourceSearchRequest:
    """构造组合检索请求"""
    return SemanticResourceSearchRequest(
        terms=[query],
        resource_types=resource_types,
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
        term=reason,
        score=score,
    )
    return SemanticSearchResponse(
        status="success",
        search_id=search_id,
        terms=[query],
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
) -> MetadataAuthorizationFilter:
    """构造召回测试的资产授权过滤器"""
    return MetadataAuthorizationFilter(
        AssetAccessPolicy(
            user_id=7,
            grants=frozenset(grants),
        ),
        "doris",
        "analytics",
    )


class SemanticRecallServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证召回记录生命周期和会话隔离"""

    async def asyncSetUp(self) -> None:
        self.repo = InMemorySemanticRecallRepo()
        self.service = SemanticRecallService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
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
            query,
            build_request(query, ["column"]),
            build_response(search_id, query, score=score, reason=reason),
            [],
            datetime.now(UTC),
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
            {record.response.search_id for record in records},
            {"search_a", "search_b"},
        )
        by_id = {record.response.search_id: record for record in records}
        search_a_request = by_id["search_a"].request
        assert search_a_request is not None
        self.assertEqual(search_a_request.terms, ["本月收入"])
        self.assertEqual(by_id["search_b"].response.terms, ["订单金额"])
        self.assertIsNone(
            await self.repo.get_latest_by_query(8, self.conversation_id, "本月收入")
        )
        self.assertIsNone(
            await self.repo.get_latest_by_query(self.user_id, uuid4(), "本月收入")
        )

    async def test_postgres_repo_round_trips_combined_recall_payload(self) -> None:
        experience = build_query_experience()
        record = await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("search_a", "本月收入", score=0.8, reason="收入"),
            [experience],
            datetime.now(UTC),
        )
        session = MagicMock()
        session.flush = AsyncMock()
        repo = SemanticRecallPGRepo(cast(Any, session))

        await repo.save(record)

        snapshot = session.add.call_args.args[0]
        self.assertEqual(
            set(snapshot.request),
            {"terms", "resource_types", "limit_per_type"},
        )
        self.assertEqual(
            set(snapshot.response),
            {
                "semantic_resources",
                "query_experiences",
                "query_experiences_retrieved_at",
            },
        )
        self.assertNotIn("search_id", snapshot.response["semantic_resources"])
        self.assertEqual(SemanticRecallPGRepo._to_record(snapshot), record)
        invalid_payload = record.model_dump()
        invalid_payload["query_experiences_retrieved_at"] = None
        with self.assertRaises(ValidationError):
            SemanticRecallRecord.model_validate(invalid_payload)

    async def test_list_and_get_use_latest_snapshot_for_each_query(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "first")
        await self._record("search_b", "本月收入", 0.8, "second")

        records = await self.service.list(
            self.user_id,
            self.conversation_id,
            limit=10,
        )
        record = await self.service.get(
            self.user_id,
            self.conversation_id,
            "本月收入",
        )

        self.assertEqual(
            [item.response.search_id for item in records],
            ["search_b"],
        )
        self.assertEqual(record.response.search_id, "search_b")

    async def test_query_experience_cache_expires_at_one_day(self) -> None:
        retrieved_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        experience = build_query_experience()
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("search_a", "本月收入", score=0.8, reason="收入"),
            [experience],
            retrieved_at,
        )

        fresh = await self.service.get_fresh_query_experiences(
            self.user_id,
            self.conversation_id,
            "本月收入",
            now=retrieved_at + timedelta(days=1) - timedelta(microseconds=1),
        )
        expired = await self.service.get_fresh_query_experiences(
            self.user_id,
            self.conversation_id,
            "本月收入",
            now=retrieved_at + timedelta(days=1),
        )

        self.assertEqual(fresh, ([experience], retrieved_at))
        self.assertIsNone(expired)

    async def test_merge_absorbs_resources_without_source_experiences(self) -> None:
        target_experience = build_query_experience(column="amount")
        source_experience = build_query_experience(column="status")
        target_retrieved_at = datetime.now(UTC)
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("search_a", "本月收入", score=0.4, reason="query_a"),
            [target_experience],
            target_retrieved_at,
        )
        source_response = build_response(
            "search_b",
            "订单金额",
            score=0.8,
            reason="query_b",
        )
        source_response.columns[0].name = "status"
        source_response.columns[0].examples = ["paid"]
        source_response.values[0].c_name = "status"
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "订单金额",
            build_request("订单金额", ["column"]),
            source_response,
            [source_experience],
            datetime.now(UTC),
        )

        merged = await self.service.merge(
            self.user_id,
            self.conversation_id,
            "本月收入",
            "订单金额",
        )

        self.assertEqual(merged.query, "本月收入")
        self.assertEqual(merged.source_queries, ["订单金额"])
        self.assertEqual(merged.response.terms, ["本月收入", "订单金额"])
        self.assertEqual(len(merged.response.metrics), 1)
        self.assertEqual(merged.response.metrics[0].rank_score, 0.8)
        self.assertEqual(
            [reason.term for reason in merged.response.metrics[0].match_reasons],
            ["query_a", "query_b"],
        )
        self.assertEqual(
            [item.name for item in merged.response.columns],
            ["status", "amount"],
        )
        self.assertEqual(merged.query_experiences, [target_experience])
        self.assertEqual(
            merged.query_experiences_retrieved_at,
            target_retrieved_at,
        )
        self.assertIsNotNone(
            await self.repo.get_latest_by_query(
                self.user_id,
                self.conversation_id,
                "本月收入",
            )
        )
        self.assertIsNone(
            await self.repo.get_latest_by_query(
                self.user_id,
                self.conversation_id,
                "订单金额",
            )
        )

        continued = await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("search_c", "本月收入", score=0.6, reason="query_c"),
            [target_experience],
            target_retrieved_at,
        )

        self.assertEqual(continued.source_queries, ["订单金额"])
        self.assertEqual(continued.query_experiences, [target_experience])
        self.assertEqual(
            [item.name for item in continued.response.columns],
            ["status", "amount"],
        )

    async def test_delete_reports_missing_records(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")
        await self._record("search_b", "本月收入", 0.8, "query_b")

        deleted, missing = await self.service.delete(
            self.user_id,
            self.conversation_id,
            ["本月收入", "unknown", "本月收入"],
        )

        self.assertEqual(deleted, ["本月收入"])
        self.assertEqual(missing, ["unknown"])
        self.assertIsNone(
            await self.repo.get_latest_by_query(
                self.user_id,
                self.conversation_id,
                "本月收入",
            )
        )
        self.assertFalse(
            any(record.query == "本月收入" for record in self.repo.records.values())
        )

    async def test_merge_rejects_missing_record(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")

        with self.assertRaises(SemanticQueriesNotFoundError) as context:
            await self.service.merge(
                self.user_id,
                self.conversation_id,
                "本月收入",
                "unknown",
            )

        self.assertEqual(context.exception.queries, ["unknown"])

    async def test_delete_all_removes_only_target_conversation(self) -> None:
        await self._record("search_a", "本月收入", 0.4, "query_a")
        other_conversation_id = uuid4()
        await self.service.record_search(
            self.user_id,
            other_conversation_id,
            "其他查询",
            build_request("其他查询", ["column"]),
            build_response(
                "search_other",
                "其他查询",
                score=0.5,
                reason="other",
            ),
            [],
            datetime.now(UTC),
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
            await self.repo.get_latest_by_query(
                self.user_id,
                other_conversation_id,
                "其他查询",
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
            "本月收入",
            build_request("本月收入", ["column"]),
            first,
            [],
            datetime.now(UTC),
        )
        await self.service.record_search(
            self.user_id,
            self.conversation_id,
            "订单状态",
            build_request("订单状态", ["value"]),
            second,
            [],
            datetime.now(UTC),
        )
        restricted = SemanticRecallService(
            recall_repo(self.repo),
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
            "本月收入",
        )
        listed = await restricted.list(
            self.user_id,
            self.conversation_id,
            limit=10,
        )
        merged = await restricted.merge(
            self.user_id,
            self.conversation_id,
            "本月收入",
            "订单状态",
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

        persisted = await self.repo.get_latest_by_query(
            self.user_id,
            self.conversation_id,
            "本月收入",
        )
        assert persisted is not None
        self.assertEqual(persisted.response.columns, [])
        self.assertEqual(persisted.response.warnings, [])


class SemanticRecallToolTest(unittest.IsolatedAsyncioTestCase):
    """验证模型可见参数和工具运行时注入"""

    def test_runtime_is_hidden_from_tool_call_schema(self) -> None:
        for semantic_tool in (search_context, list_recalls):
            schema = cast(type[BaseModel], semantic_tool.tool_call_schema)
            properties = schema.model_json_schema().get(
                "properties",
                {},
            )
            self.assertNotIn("runtime", properties)
        search_schema = cast(
            type[BaseModel],
            search_context.tool_call_schema,
        ).model_json_schema()
        search_properties = search_schema["properties"]
        self.assertNotIn("table_names", search_properties)
        self.assertNotIn("include_relations", search_properties)
        self.assertIn("resource_types", search_schema["required"])
        self.assertIn("terms", search_schema["required"])
        get_schema = cast(type[BaseModel], get_recall.tool_call_schema)
        merge_schema = cast(type[BaseModel], merge_recalls.tool_call_schema)
        delete_schema = cast(type[BaseModel], delete_recalls.tool_call_schema)
        self.assertEqual(set(get_schema.model_fields), {"query"})
        self.assertEqual(
            set(merge_schema.model_fields),
            {"target_query", "source_query"},
        )
        self.assertEqual(set(delete_schema.model_fields), {"queries"})

    def test_reference_loader_rejects_noncanonical_query(self) -> None:
        message = ToolMessage(
            id="message-1",
            tool_call_id="call-1",
            name="search_context",
            content=json.dumps({"status": "stored", "query": " revenue "}),
        )

        self.assertIsNone(parse_semantic_recall_reference(message))

    async def test_merged_source_queries_are_hidden_from_model_payloads(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        service = SemanticRecallService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
        )
        conversation_id = uuid4()
        for query, search_id in (("订单金额", "search_a"), ("本月收入", "search_b")):
            await service.record_search(
                7,
                conversation_id,
                query,
                build_request(query, ["column"]),
                build_response(search_id, query, score=0.8, reason=query),
                [],
                datetime.now(UTC),
            )
        merged = await service.merge(7, conversation_id, "本月收入", "订单金额")

        summary = _record_summary(merged)
        expanded = json.loads(_expanded_content(merged, "record"))

        self.assertEqual(merged.source_queries, ["订单金额"])
        self.assertNotIn("source_queries", summary)
        self.assertNotIn("source_queries", expanded["recall"])

    async def test_search_tool_uses_query_for_experiences_and_terms_for_resources(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        conversation_id = uuid4()
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset({_CONFIGURED_DATABASE_GRANT}),
        )
        response = build_response(
            "search_a",
            "收入",
            score=0.8,
            reason="收入",
        )
        response.terms = ["收入", "订单金额"]
        experience = build_query_experience()
        auth_repo = MagicMock()
        auth_repo.get_user_by_id = AsyncMock(
            return_value=SimpleNamespace(doris_role_name="analyst")
        )
        authorization_service = MagicMock()
        authorization_service.get_asset_policy = AsyncMock(return_value=policy)
        meta_search_service = MagicMock()
        second_response = response.model_copy(
            deep=True,
            update={"search_id": "search_b", "terms": ["订单状态"]},
        )
        second_response.columns[0].name = "status"
        second_response.columns[0].examples = ["paid"]
        meta_search_service.search = AsyncMock(side_effect=[response, second_response])
        experience_service = MagicMock()
        experience_service.search = AsyncMock(return_value=[experience])
        runtime = SimpleNamespace(
            config={
                "configurable": {
                    "user_id": 7,
                    "conversation_id": str(conversation_id),
                }
            }
        )

        with (
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall.AuthPGRepo",
                return_value=auth_repo,
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "AuthorizationService",
                return_value=authorization_service,
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall.MetaSearchService",
                return_value=meta_search_service,
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "build_query_experience_service",
                return_value=experience_service,
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "auth_postgres_client_manager.session",
                side_effect=lambda: object_context(MagicMock()),
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "meta_postgres_client_manager.session",
                side_effect=[
                    object_context(MagicMock()),
                    object_context(MagicMock()),
                    object_context(MagicMock()),
                ],
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "semantic_recall_repository",
                side_effect=lambda: recall_repository_context(repo),
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "embedding_client_manager.get_client",
                return_value=MagicMock(),
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "es_client_manager.get_client",
                return_value=MagicMock(),
            ),
        ):
            first_result = await cast(Any, search_context).coroutine(
                runtime=runtime,
                resource_types=["column", "metric"],
                query="统计本月订单收入",
                terms=["收入", "订单金额"],
            )
            first_stored = await repo.get_latest_by_query(
                7,
                conversation_id,
                "统计本月订单收入",
            )
            second_result = await cast(Any, search_context).coroutine(
                runtime=runtime,
                resource_types=["column", "metric"],
                query="统计本月订单收入",
                terms=["订单状态"],
            )

        resource_requests = [
            call.args[0] for call in meta_search_service.search.await_args_list
        ]
        self.assertTrue(all(not hasattr(item, "query") for item in resource_requests))
        self.assertEqual(
            [item.terms for item in resource_requests],
            [["收入", "订单金额"], ["订单状态"]],
        )
        experience_service.search.assert_awaited_once_with(
            user_id=7,
            role_name="analyst",
            policy=policy,
            query="统计本月订单收入",
            table_names={"orders"},
            column_keys={("orders", "amount")},
            limit=3,
        )
        self.assertEqual(
            first_result,
            {"status": "stored", "query": "统计本月订单收入"},
        )
        self.assertEqual(
            second_result,
            {"status": "stored", "query": "统计本月订单收入"},
        )
        second_stored = await repo.get_latest_by_query(
            7,
            conversation_id,
            "统计本月订单收入",
        )
        assert first_stored is not None
        assert second_stored is not None
        assert second_stored.request is not None
        self.assertEqual(second_stored.query, "统计本月订单收入")
        self.assertEqual(
            second_stored.response.terms,
            ["收入", "订单金额", "订单状态"],
        )
        self.assertEqual(
            [item.name for item in second_stored.response.columns],
            ["amount", "status"],
        )
        self.assertEqual(second_stored.query_experiences, [experience])
        self.assertEqual(
            second_stored.query_experiences_retrieved_at,
            first_stored.query_experiences_retrieved_at,
        )

    async def test_tool_message_persists_reference_and_model_sees_authorized_record(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        service_with_full_database_grant = SemanticRecallService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
        )
        conversation_id = uuid4()
        experience = build_query_experience(column="status")
        record = await service_with_full_database_grant.record_search(
            7,
            conversation_id,
            "revenue",
            build_request("revenue", ["column"]),
            build_response("search_a", "revenue", score=0.8, reason="query"),
            [experience],
            datetime.now(UTC),
        )
        reference_content = json.dumps(
            semantic_recall_reference(record),
            ensure_ascii=False,
        )
        old_reference = ToolMessage(
            id="old_message",
            tool_call_id="old_call",
            name="search_context",
            content=reference_content,
        )
        current_reference = ToolMessage(
            id="current_message",
            tool_call_id="current_call",
            name="search_context",
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
            runtime=Runtime(),
        )
        restricted_service = SemanticRecallService(
            recall_repo(repo),
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
                "app.analytics.agents.explorer.semantic_recall_middleware.get_config",
                return_value={
                    "configurable": {
                        "user_id": 7,
                        "conversation_id": str(conversation_id),
                    }
                },
            ),
            patch(
                "app.analytics.agents.explorer.semantic_recall_middleware."
                "create_authorized_semantic_recall_service",
                new=AsyncMock(return_value=restricted_service),
            ),
            patch(
                "app.analytics.agents.explorer.semantic_recall_middleware."
                "semantic_recall_repository",
                return_value=recall_repository_context(repo),
            ),
        ):
            await SemanticRecallExpansionMiddleware().awrap_model_call(
                request,
                handler,
            )

        self.assertEqual(current_reference.content, reference_content)
        self.assertNotIn("recall_id", reference_content)
        self.assertNotIn("amount", reference_content)
        self.assertNotIn("SELECT status", reference_content)
        self.assertEqual(getattr(seen_messages[0], "content", None), reference_content)
        expanded_content = str(getattr(seen_messages[2], "content", ""))
        self.assertNotIn("search_id", expanded_content)
        self.assertNotIn("recall_id", expanded_content)
        self.assertNotIn("amount", expanded_content)
        self.assertIn("paid", expanded_content)
        self.assertIn("SELECT status", expanded_content)

    async def test_get_tool_writes_only_recall_reference_to_state(self) -> None:
        repo = InMemorySemanticRecallRepo()
        service = SemanticRecallService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
        )
        conversation_id = uuid4()
        await service.record_search(
            1,
            conversation_id,
            "revenue",
            build_request("revenue", ["column"]),
            build_response("search_a", "revenue", score=0.8, reason="query"),
            [],
            datetime.now(UTC),
        )
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([get_recall]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        with (
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "create_authorized_semantic_recall_service",
                new=AsyncMock(return_value=service),
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "semantic_recall_repository",
                return_value=recall_repository_context(repo),
            ),
        ):
            result = await graph.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "get_recall",
                                    "args": {"query": "revenue"},
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
        self.assertEqual(payload["query"], "revenue")
        self.assertEqual(payload["status"], "stored")
        self.assertNotIn("recall_id", payload)
        self.assertNotIn("semantic_recall", payload)
        self.assertNotIn("amount", content)

    async def test_tool_node_injects_conversation_context(self) -> None:
        repo = InMemorySemanticRecallRepo()
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([list_recalls]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        conversation_id = uuid4()

        service = SemanticRecallService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
        )
        with (
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "create_authorized_semantic_recall_service",
                new=AsyncMock(return_value=service),
            ),
            patch(
                "app.analytics.agents.explorer.tools.semantic_recall."
                "semantic_recall_repository",
                return_value=recall_repository_context(repo),
            ),
        ):
            result = await graph.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "list_recalls",
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
        self.assertNotIn("query", SemanticResourceSearchRequest.model_fields)
        self.assertNotIn("table_names", SemanticResourceSearchRequest.model_fields)
        for method in (
            ColumnESRepo.search_text_hits,
            ColumnESRepo.search_vector_hits,
            ValueESRepo.search_hits,
        ):
            self.assertNotIn("table_names", inspect.signature(method).parameters)

    def test_resource_terms_require_nonempty_normalized_value(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticResourceSearchRequest(
                terms=["", "  "],
                resource_types=["column"],
            )


if __name__ == "__main__":
    unittest.main()
