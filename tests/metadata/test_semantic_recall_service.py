"""语义召回记录管理测试。"""

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
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError
from sqlalchemy.dialects import postgresql

from app.assistant.agents.explorer.semantic_recall_handler import _record_summary
from app.assistant.agents.explorer.semantic_recall_messages import (
    expand_semantic_recall_messages_for_display,
)
from app.assistant.agents.explorer.semantic_recall_protocol import (
    parse_semantic_recall_references,
    semantic_recall_payload,
    semantic_recall_reference,
)
from app.assistant.agents.explorer.tools import create_semantic_recall_tools
from app.assistant.agents.middleware.semantic_recall_expansion import (
    SemanticRecallExpansionMiddleware,
)
from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
)
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMatchReason,
    SemanticMetricRecallResult,
    SemanticRecallFailure,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticResourceType,
    SemanticTableContext,
    SemanticValueRecallResult,
)
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticQueriesNotFoundError,
    SemanticRecallContextService,
)
from app.shared.contracts.query_experience import (
    QueryAssetSnapshot,
    QueryExperienceRecall,
    QueryExperienceRecallResult,
)

_FULL_DATABASE_GRANT = AssetIdentity("doris", "analytics")
_CONFIGURED_DATABASE_GRANT = AssetIdentity("doris", "ecommerce")

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime

_RECALL = MagicMock(spec=SemanticRecallRuntime)

_SEMANTIC_RECALL_TOOLS = {
    semantic_tool.name: semantic_tool
    for semantic_tool in create_semantic_recall_tools(recall=_RECALL)
}
recall_context = _SEMANTIC_RECALL_TOOLS["recall_context"]
list_recalls = _SEMANTIC_RECALL_TOOLS["list_recalls"]
get_recall = _SEMANTIC_RECALL_TOOLS["get_recall"]
merge_recalls = _SEMANTIC_RECALL_TOOLS["merge_recalls"]
delete_recalls = _SEMANTIC_RECALL_TOOLS["delete_recalls"]


class InMemorySemanticRecallRepo:
    """为服务与工具单元测试提供进程内召回仓储。"""

    def __init__(self) -> None:
        """初始化空召回记录集合。"""
        self.records: dict[tuple[int, object, str], SemanticRecallRecord] = {}

    async def acquire_query_lock(
        self,
        user_id: int,
        conversation_id: object,
        query: str,
    ) -> None:
        """进程内仓储不需要额外的事务锁。"""
        del user_id, conversation_id, query

    async def save(self, record: SemanticRecallRecord) -> None:
        """保存召回记录。"""
        self.records[
            (record.user_id, record.conversation_id, record.response.recall_id)
        ] = record

    async def get_latest_by_query(
        self,
        user_id: int,
        conversation_id: object,
        query: str,
    ) -> SemanticRecallRecord | None:
        """获取指定 query 的最新召回记录。"""
        records = [
            record
            for (owner_id, owner_conversation_id, _), record in self.records.items()
            if owner_id == user_id
            and owner_conversation_id == conversation_id
            and record.query == query
        ]
        return max(
            records,
            key=lambda record: (record.updated_at, record.response.recall_id),
            default=None,
        )

    async def get_latest_by_queries(
        self, user_id: int, conversation_id: object, queries: list[str]
    ) -> list[SemanticRecallRecord]:
        """模拟批量查询的最新快照语义。"""
        return [
            record
            for query in dict.fromkeys(queries)
            if (
                record := await self.get_latest_by_query(
                    user_id, conversation_id, query
                )
            )
            is not None
        ]

    async def list(
        self,
        user_id: int,
        conversation_id: object,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[SemanticRecallRecord]:
        """按更新时间倒序列出每个 query 的最新召回记录。"""
        latest_by_query: dict[str, SemanticRecallRecord] = {}
        for (owner_id, owner_conversation_id, _), record in self.records.items():
            if owner_id != user_id or owner_conversation_id != conversation_id:
                continue
            current = latest_by_query.get(record.query)
            if current is None or (record.updated_at, record.response.recall_id) > (
                current.updated_at,
                current.response.recall_id,
            ):
                latest_by_query[record.query] = record
        records = sorted(
            latest_by_query.values(),
            key=lambda record: (record.updated_at, record.response.recall_id),
            reverse=True,
        )
        return records[offset : offset + limit]

    async def delete_by_query(
        self,
        user_id: int,
        conversation_id: object,
        query: str,
    ) -> bool:
        """删除 query 的全部召回记录。"""
        keys = [
            key
            for key, record in self.records.items()
            if key[:2] == (user_id, conversation_id) and record.query == query
        ]
        for key in keys:
            del self.records[key]
        return bool(keys)

    async def delete_all(self, user_id: int, conversation_id: object) -> None:
        """删除会话全部召回记录。"""
        self.records = {
            key: value
            for key, value in self.records.items()
            if key[:2] != (user_id, conversation_id)
        }


def recall_repo(repo: InMemorySemanticRecallRepo) -> SemanticRecallPGRepo:
    """将测试仓储收窄为服务声明的具体仓储类型。"""
    return cast(SemanticRecallPGRepo, repo)


@asynccontextmanager
async def object_context(value: Any) -> AsyncGenerator[Any]:
    """为工具依赖提供简单异步上下文。"""
    yield value


def build_query_experience(
    *,
    table: str = "orders",
    column: str = "amount",
) -> QueryExperienceRecallResult:
    """构造紧凑查询经验结果。"""
    return QueryExperienceRecallResult(
        id=uuid4(),
        purpose="查询订单收入",
        sql_template=f"SELECT {column} FROM {table}",
        assets=[
            QueryAssetSnapshot(
                kind="column",
                database="analytics",
                table=table,
                column=column,
                meta_version=1,
            )
        ],
    )


def build_request(
    query: str,
    resource_types: list[SemanticResourceType],
) -> SemanticResourceRecallRequest:
    """构造组合检索请求。"""
    return SemanticResourceRecallRequest(
        terms=[query],
        resource_types=resource_types,
    )


def build_response(
    recall_id: str,
    query: str,
    *,
    score: float,
    reason: str,
) -> SemanticResourceRecallResponse:
    """构造包含重复资源的测试召回响应。"""
    match_reason = SemanticMatchReason(
        match_type="fulltext",
        term=reason,
        score=score,
    )
    return SemanticResourceRecallResponse(
        status="success",
        recall_id=recall_id,
        terms=[query],
        metrics=[
            SemanticMetricRecallResult(
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
            SemanticColumnRecallResult(
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
            SemanticValueRecallResult(
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
        failures=[],
        warnings=[],
        truncated=False,
    )


def build_authorization_filter(
    *grants: AssetIdentity,
) -> MetadataAuthorizationFilter:
    """构造召回测试的资产授权过滤器。"""
    return MetadataAuthorizationFilter(
        AssetAccessPolicy(
            user_id=7,
            grants=frozenset(grants),
        ),
        "doris",
        "analytics",
    )


class SemanticRecallContextServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证召回记录生命周期和会话隔离。"""

    async def asyncSetUp(self) -> None:
        self.repo = InMemorySemanticRecallRepo()
        self.service = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        self.user_id = 7
        self.conversation_id = uuid4()

    async def _record(
        self,
        recall_id: str,
        query: str,
        score: float,
        reason: str,
    ) -> None:
        await self.service.record(
            self.user_id,
            self.conversation_id,
            query,
            build_request(query, ["column"]),
            build_response(recall_id, query, score=score, reason=reason),
            [],
            datetime.now(UTC),
        )

    async def test_batch_expansion_preserves_order_duplicates_missing_and_current_permissions(
        self,
    ):
        await self._record("old_a", "A", 0.4, "old")
        await self._record("new_a", "A", 0.8, "new")
        await self._record("record_b", "B", 0.5, "second")
        restricted = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        messages = [
            ToolMessage(
                name="get_recall",
                tool_call_id=str(index),
                content=json.dumps({"status": "stored", "query": query}),
            )
            for index, query in enumerate(["B", "A", "missing", "A"])
        ]
        original_contents = [message.content for message in messages]
        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args: object_context(restricted),
            ) as context,
            patch.object(
                self.repo,
                "get_latest_by_queries",
                wraps=self.repo.get_latest_by_queries,
            ) as batch,
        ):
            expanded = await expand_semantic_recall_messages_for_display(
                messages, self.user_id, self.conversation_id, recall=_RECALL
            )
        context.assert_called_once_with(self.user_id)
        batch.assert_awaited_once_with(
            self.user_id, self.conversation_id, ["B", "A", "missing"]
        )
        self.assertEqual(
            [message.tool_call_id for message in expanded], ["0", "1", "2", "3"]
        )
        payloads = [json.loads(message.content) for message in expanded]
        self.assertEqual([payloads[i]["query"] for i in (0, 1, 3)], ["B", "A", "A"])
        self.assertEqual(payloads[1], payloads[3])
        self.assertEqual(payloads[2]["queries"], ["missing"])
        self.assertEqual(payloads[2]["status"], "error")
        for index in (0, 1, 3):
            self.assertEqual(payloads[index]["tables"], {})
            self.assertEqual(payloads[index]["metrics"], {})
        self.assertEqual([message.content for message in messages], original_contents)
        records = await restricted.get_many(
            self.user_id, self.conversation_id, ["A", "B", "missing"]
        )
        self.assertEqual(records["A"].response.recall_id, "new_a")
        self.assertEqual(
            await restricted.get_many(self.user_id + 1, self.conversation_id, ["A"]), {}
        )
        self.assertEqual(await restricted.get_many(self.user_id, uuid4(), ["A"]), {})

    async def test_batch_repo_uses_one_scoped_latest_per_query_statement(self):
        session = MagicMock(scalars=AsyncMock(return_value=MagicMock(all=list)))
        repo = SemanticRecallPGRepo(session)
        self.assertEqual(
            await repo.get_latest_by_queries(
                self.user_id, self.conversation_id, ["A", "B", "A"]
            ),
            [],
        )
        session.scalars.assert_awaited_once()
        statement = session.scalars.await_args.args[0].compile(
            dialect=postgresql.dialect()
        )
        sql = str(statement)
        self.assertIn("DISTINCT ON (semantic_recall_snapshots.query)", sql)
        self.assertIn("semantic_recall_snapshots.user_id =", sql)
        self.assertIn("semantic_recall_snapshots.conversation_id =", sql)
        self.assertIn(
            "ORDER BY semantic_recall_snapshots.query, semantic_recall_snapshots.updated_at DESC, semantic_recall_snapshots.recall_id DESC",
            sql,
        )
        self.assertIn(["A", "B"], statement.params.values())
        self.assertIn(self.user_id, statement.params.values())
        self.assertIn(self.conversation_id, statement.params.values())

    async def test_empty_batch_does_not_query_database(self):
        session = MagicMock(scalars=AsyncMock())
        self.assertEqual(
            await SemanticRecallPGRepo(session).get_latest_by_queries(
                self.user_id, self.conversation_id, []
            ),
            [],
        )
        session.scalars.assert_not_awaited()

    async def test_delete_tool_persists_references_and_rechecks_permissions_on_both_read_paths(
        self,
    ):
        await self._record("record_a", "A", 0.8, "first")
        await self._record("record_b", "B", 0.8, "second")
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([delete_recalls]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        config: RunnableConfig = {
            "configurable": {
                "user_id": self.user_id,
                "conversation_id": str(self.conversation_id),
            }
        }
        with patch.object(
            _RECALL,
            "context_service",
            side_effect=lambda *args: object_context(self.service),
        ):
            result = await builder.compile().ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "delete_recalls",
                                    "id": "delete_call",
                                    "type": "tool_call",
                                    "args": {
                                        "deletions": [
                                            {"query": "A", "metrics": {"revenue": {}}},
                                            {"query": "B"},
                                        ]
                                    },
                                }
                            ],
                        )
                    ]
                },
                config,
            )
            message = result["messages"][-1]
            original = message.content
            visible = await expand_semantic_recall_messages_for_display(
                [message], self.user_id, self.conversation_id, recall=_RECALL
            )
        self.assertEqual(
            json.loads(original),
            {
                "status": "success",
                "recalls": [
                    {"status": "stored", "query": "A"},
                    {"status": "deleted", "query": "B"},
                ],
            },
        )
        self.assertIn("orders", json.loads(visible[0].content)["recalls"][0]["tables"])
        self.assertNotIn("amount", original)
        self.assertIsNone(
            await self.repo.get_latest_by_query(self.user_id, self.conversation_id, "B")
        )
        # 同名 query 重建也不能把过去的删除确认变成新内容。
        await self._record("recreated_b", "B", 0.8, "new")
        revoked = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=[HumanMessage(content="继续"), message],
            runtime=Runtime(),
        )
        seen = []

        async def model_handler(model_request):
            seen.extend(model_request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args: object_context(revoked),
            ),
            patch.object(
                self.repo,
                "get_latest_by_queries",
                wraps=self.repo.get_latest_by_queries,
            ) as batch,
            patch(
                "app.assistant.agents.middleware.semantic_recall_expansion.get_config",
                return_value=config,
            ),
        ):
            await SemanticRecallExpansionMiddleware(_RECALL).awrap_model_call(
                request, model_handler
            )
            displayed = await expand_semantic_recall_messages_for_display(
                [message], self.user_id, self.conversation_id, recall=_RECALL
            )
        self.assertEqual(batch.await_count, 2)
        for call in batch.await_args_list:
            self.assertEqual(call.args, (self.user_id, self.conversation_id, ["A"]))
        for content in (seen[-1].content, displayed[0].content):
            payload = json.loads(content)
            self.assertEqual(payload["recalls"][0]["tables"], {})
            self.assertEqual(payload["recalls"][0]["metrics"], {})
            self.assertEqual(payload["recalls"][0]["query_experiences"], [])
            self.assertEqual(payload["recalls"][1], {"status": "deleted", "query": "B"})
        self.assertEqual(message.content, original)
        await self.repo.delete_by_query(self.user_id, self.conversation_id, "A")
        with patch.object(
            _RECALL,
            "context_service",
            side_effect=lambda *args: object_context(revoked),
        ):
            missing = await expand_semantic_recall_messages_for_display(
                [message], self.user_id, self.conversation_id, recall=_RECALL
            )
        results = json.loads(missing[0].content)["recalls"]
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(results[0]["queries"], ["A"])
        self.assertEqual(results[1], {"status": "deleted", "query": "B"})

    async def test_whole_deletion_confirmation_does_not_read_snapshots(self):
        message = ToolMessage(
            name="delete_recalls",
            tool_call_id="delete",
            content=json.dumps(
                {
                    "status": "success",
                    "recalls": [{"status": "deleted", "query": "A"}],
                }
            ),
        )
        with patch.object(
            _RECALL, "context_service", side_effect=AssertionError("unexpected read")
        ) as context:
            displayed = await expand_semantic_recall_messages_for_display(
                [message], self.user_id, self.conversation_id, recall=_RECALL
            )
        context.assert_not_called()
        self.assertEqual(
            json.loads(displayed[0].content), json.loads(str(message.content))
        )

    async def test_failed_batch_expansion_preserves_deletion_confirmation(self):
        message = ToolMessage(
            name="delete_recalls",
            tool_call_id="delete",
            content=json.dumps(
                {
                    "status": "success",
                    "recalls": [
                        {"status": "stored", "query": "A"},
                        {"status": "deleted", "query": "B"},
                    ],
                }
            ),
        )
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=[HumanMessage(content="继续"), message],
            runtime=Runtime(),
        )
        seen = []

        async def model_handler(model_request):
            seen.extend(model_request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=RuntimeError("authorization unavailable"),
            ),
            patch(
                "app.assistant.agents.middleware.semantic_recall_expansion.get_config",
                return_value={
                    "configurable": {
                        "user_id": self.user_id,
                        "conversation_id": str(self.conversation_id),
                    }
                },
            ),
        ):
            await SemanticRecallExpansionMiddleware(_RECALL).awrap_model_call(
                request, model_handler
            )
            displayed = await expand_semantic_recall_messages_for_display(
                [message], self.user_id, self.conversation_id, recall=_RECALL
            )
        results = json.loads(seen[-1].content)["recalls"]
        self.assertEqual(
            results[0], {"status": "error", "message": "语义召回记录暂不可用"}
        )
        self.assertEqual(results[1], {"status": "deleted", "query": "B"})
        self.assertEqual(displayed[0].content, message.content)

    async def test_each_search_is_persisted_with_request_and_result(self) -> None:
        await self._record("recall_a", "本月收入", 0.4, "query_a")
        await self._record("recall_b", "订单金额", 0.8, "query_b")

        records = await self.service.list(
            self.user_id,
            self.conversation_id,
            limit=10,
        )

        self.assertEqual(
            {record.response.recall_id for record in records},
            {"recall_a", "recall_b"},
        )
        by_id = {record.response.recall_id: record for record in records}
        recall_a_request = by_id["recall_a"].request
        assert recall_a_request is not None
        self.assertEqual(recall_a_request.terms, ["本月收入"])
        self.assertEqual(by_id["recall_b"].response.terms, ["订单金额"])
        self.assertIsNone(
            await self.repo.get_latest_by_query(8, self.conversation_id, "本月收入")
        )
        self.assertIsNone(
            await self.repo.get_latest_by_query(self.user_id, uuid4(), "本月收入")
        )

    async def test_postgres_repo_round_trips_combined_recall_payload(self) -> None:
        experience = build_query_experience()
        record = await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_a", "本月收入", score=0.8, reason="收入"),
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
                "query_experience_role_name",
                "query_experience_authorization_fingerprint",
            },
        )
        self.assertNotIn("recall_id", snapshot.response["semantic_resources"])
        self.assertEqual(SemanticRecallPGRepo._to_record(snapshot), record)
        invalid_payload = record.model_dump()
        invalid_payload["query_experiences_retrieved_at"] = None
        with self.assertRaises(ValidationError):
            SemanticRecallRecord.model_validate(invalid_payload)

    async def test_list_and_get_use_latest_snapshot_for_each_query(self) -> None:
        await self._record("recall_a", "本月收入", 0.4, "first")
        first = await self.service.get(
            self.user_id,
            self.conversation_id,
            "本月收入",
        )
        await self._record("recall_b", "本月收入", 0.8, "second")

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
            [item.response.recall_id for item in records],
            ["recall_b"],
        )
        self.assertEqual(record.response.recall_id, "recall_b")
        self.assertEqual(record.created_at, first.created_at)
        self.assertGreaterEqual(record.updated_at, first.updated_at)

    async def test_successful_refresh_clears_matching_failure(self) -> None:
        failed = build_response("recall_a", "本月收入", score=0.4, reason="first")
        failed.status = "partial"
        failed.failures = [
            SemanticRecallFailure(
                resource_type="column",
                channel="fulltext",
                term="本月收入",
            )
        ]
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            failed,
            [],
            datetime.now(UTC),
        )

        refreshed = await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_b", "本月收入", score=0.8, reason="second"),
            [],
            datetime.now(UTC),
        )

        self.assertEqual(refreshed.response.status, "success")
        self.assertEqual(refreshed.response.failures, [])

    async def test_refresh_for_other_term_keeps_failure(self) -> None:
        failed = build_response("recall_a", "本月收入", score=0.4, reason="first")
        failed.status = "partial"
        failed.failures = [
            SemanticRecallFailure(
                resource_type="column",
                channel="fulltext",
                term="本月收入",
            )
        ]
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "收入分析",
            build_request("本月收入", ["column"]),
            failed,
            [],
            datetime.now(UTC),
        )

        refreshed = await self.service.record(
            self.user_id,
            self.conversation_id,
            "收入分析",
            build_request("GMV", ["column"]),
            build_response("recall_b", "GMV", score=0.8, reason="second"),
            [],
            datetime.now(UTC),
        )

        self.assertEqual(refreshed.response.status, "partial")
        self.assertEqual(refreshed.response.failures, failed.failures)

    async def test_query_experience_cache_expires_at_one_day(self) -> None:
        retrieved_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        experience = build_query_experience()
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_a", "本月收入", score=0.8, reason="收入"),
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

    async def test_query_experience_cache_requires_matching_role_and_fingerprint(
        self,
    ) -> None:
        role_fingerprint = "a" * 64
        scoped = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name="analyst",
            query_experience_authorization_fingerprint=role_fingerprint,
        )
        experience = build_query_experience()
        retrieved_at = datetime.now(UTC)
        await scoped.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_a", "本月收入", score=0.8, reason="收入"),
            [experience],
            retrieved_at,
        )

        fresh = await scoped.get_fresh_query_experiences(
            self.user_id,
            self.conversation_id,
            "本月收入",
        )
        changed_role_service = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name="finance",
            query_experience_authorization_fingerprint=role_fingerprint,
        )
        changed_role = await changed_role_service.get_fresh_query_experiences(
            self.user_id,
            self.conversation_id,
            "本月收入",
        )
        changed_fingerprint_service = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name="analyst",
            query_experience_authorization_fingerprint="b" * 64,
        )
        changed_fingerprint = (
            await changed_fingerprint_service.get_fresh_query_experiences(
                self.user_id,
                self.conversation_id,
                "本月收入",
            )
        )

        self.assertEqual(fresh, ([experience], retrieved_at))
        self.assertIsNone(changed_role)
        self.assertIsNone(changed_fingerprint)
        restored = await scoped.get_fresh_query_experiences(
            self.user_id, self.conversation_id, "本月收入"
        )
        self.assertEqual(restored, fresh)

    async def test_merge_absorbs_resources_without_source_experiences(self) -> None:
        target_experience = build_query_experience(column="amount")
        source_experience = build_query_experience(column="status")
        target_retrieved_at = datetime.now(UTC)
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_a", "本月收入", score=0.4, reason="query_a"),
            [target_experience],
            target_retrieved_at,
        )
        source_response = build_response(
            "recall_b",
            "订单金额",
            score=0.8,
            reason="query_b",
        )
        source_response.columns[0].name = "status"
        source_response.columns[0].examples = ["paid"]
        source_response.values[0].c_name = "status"
        await self.service.record(
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
            ["query_b"],
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

        continued = await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            build_response("recall_c", "本月收入", score=0.6, reason="query_c"),
            [target_experience],
            target_retrieved_at,
        )

        self.assertEqual(continued.source_queries, ["订单金额"])
        self.assertEqual(continued.query_experiences, [target_experience])
        self.assertEqual(
            [item.name for item in continued.response.columns],
            ["status", "amount"],
        )

    async def test_newer_metadata_version_replaces_same_resource_snapshot(
        self,
    ) -> None:
        previous = build_response("recall_a", "收入", score=0.9, reason="old")
        latest = build_response("recall_b", "收入", score=0.2, reason="new")
        latest.metrics[0].description = "最新收入定义"
        latest.metrics[0].alias = ["最新指标别名"]
        latest.metrics[0].relevant_columns = [{"t_name": "orders", "c_name": "status"}]
        latest.metrics[0].meta_version = 2
        latest.metrics[0].index_version = 1
        latest.metrics[0].index_status = "stale"
        latest.columns[0].description = "最新订单金额定义"
        latest.columns[0].alias = ["最新字段别名"]
        latest.columns[0].examples = ["new"]
        latest.columns[0].reference_t_name = "customers"
        latest.columns[0].reference_c_name = "id"
        latest.columns[0].inclusion_reasons = ["latest"]
        latest.columns[0].meta_version = 2
        latest.columns[0].index_version = 1
        latest.columns[0].index_status = "stale"

        await self.service.record(
            self.user_id,
            self.conversation_id,
            "收入",
            build_request("收入", ["column", "metric"]),
            previous,
            [],
            datetime.now(UTC),
        )
        record = await self.service.record(
            self.user_id,
            self.conversation_id,
            "收入",
            build_request("收入", ["column", "metric"]),
            latest,
            [],
            datetime.now(UTC),
        )

        metric = record.response.metrics[0]
        self.assertEqual(metric.meta_version, 2)
        self.assertEqual(metric.description, "最新收入定义")
        self.assertEqual(metric.alias, ["最新指标别名"])
        self.assertEqual(metric.relevant_columns, latest.metrics[0].relevant_columns)
        self.assertEqual(metric.rank_score, 0.2)
        self.assertEqual([reason.term for reason in metric.match_reasons], ["new"])
        column = record.response.columns[0]
        self.assertEqual(column.meta_version, 2)
        self.assertEqual(column.description, "最新订单金额定义")
        self.assertEqual(column.alias, ["最新字段别名"])
        self.assertEqual(column.examples, ["new"])
        self.assertEqual(column.reference_t_name, "customers")
        self.assertEqual(column.inclusion_reasons, ["latest"])
        self.assertEqual([reason.term for reason in column.match_reasons], ["new"])

    async def test_delete_rejects_missing_records_before_mutation(self) -> None:
        await self._record("recall_a", "本月收入", 0.4, "query_a")
        await self._record("recall_b", "本月收入", 0.8, "query_b")

        with self.assertRaises(SemanticQueriesNotFoundError) as context:
            await self.service.delete(
                self.user_id,
                self.conversation_id,
                [
                    SemanticRecallResourceDeletion(query="本月收入"),
                    SemanticRecallResourceDeletion(query="unknown"),
                ],
            )

        self.assertEqual(context.exception.queries, ["unknown"])
        self.assertIsNotNone(
            await self.repo.get_latest_by_query(
                self.user_id,
                self.conversation_id,
                "本月收入",
            )
        )

        [final_record] = await self.service.delete(
            self.user_id,
            self.conversation_id,
            [SemanticRecallResourceDeletion(query="本月收入")],
        )

        self.assertEqual(final_record.response.metrics, [])
        self.assertEqual(final_record.response.columns, [])
        self.assertEqual(final_record.response.values, [])
        self.assertEqual(final_record.response.tables, [])
        self.assertEqual(final_record.query_experiences, [])
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

    async def test_delete_resources_updates_query_context(self) -> None:
        experience = build_query_experience()
        response = build_response("recall_a", "本月收入", score=0.8, reason="收入")
        response.values[0].c_name = "amount"
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column", "metric", "value"]),
            response,
            [experience],
            datetime.now(UTC),
        )

        [updated_record] = await self.service.delete(
            self.user_id,
            self.conversation_id,
            [
                SemanticRecallResourceDeletion.model_validate(
                    {
                        "query": "本月收入",
                        "tables": {
                            "orders": {"columns": {"amount": {"values": ["paid"]}}}
                        },
                        "metrics": {"revenue": {}},
                        "query_experiences": [{"id": experience.id}],
                    }
                )
            ],
        )

        self.assertEqual(updated_record.response.metrics, [])
        self.assertEqual(updated_record.response.values, [])
        self.assertEqual(len(updated_record.response.columns), 1)
        self.assertEqual(len(updated_record.response.tables), 1)
        self.assertEqual(updated_record.query_experiences, [])

        [cleared_record] = await self.service.delete(
            self.user_id,
            self.conversation_id,
            [
                SemanticRecallResourceDeletion.model_validate(
                    {
                        "query": "本月收入",
                        "tables": {"orders": {"columns": {"amount": {}}}},
                    }
                )
            ],
        )
        self.assertEqual(cleared_record.response.columns, [])
        self.assertEqual(cleared_record.response.tables, [])

    async def test_merge_rejects_missing_record(self) -> None:
        await self._record("recall_a", "本月收入", 0.4, "query_a")

        with self.assertRaises(SemanticQueriesNotFoundError) as context:
            await self.service.merge(
                self.user_id,
                self.conversation_id,
                "本月收入",
                "unknown",
            )

        self.assertEqual(context.exception.queries, ["unknown"])

    async def test_delete_all_removes_only_target_conversation(self) -> None:
        await self._record("recall_a", "本月收入", 0.4, "query_a")
        other_conversation_id = uuid4()
        await self.service.record(
            self.user_id,
            other_conversation_id,
            "其他查询",
            build_request("其他查询", ["column"]),
            build_response(
                "recall_other",
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
        first = build_response("recall_a", "本月收入", score=0.4, reason="a")
        first.columns[0].index_status = "stale"
        first.status = "partial"
        first.failures = [
            SemanticRecallFailure(
                resource_type="column",
                channel="fulltext",
                term="本月收入",
            )
        ]
        first.warnings = [
            "字段语义索引状态为 stale: orders.amount",
            "排序后的字段上下文已截断，最多保留 30 个资源",
        ]
        second = build_response("recall_b", "订单状态", score=0.8, reason="b")
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "本月收入",
            build_request("本月收入", ["column"]),
            first,
            [],
            datetime.now(UTC),
        )
        await self.service.record(
            self.user_id,
            self.conversation_id,
            "订单状态",
            build_request("订单状态", ["value"]),
            second,
            [],
            datetime.now(UTC),
        )
        restricted = SemanticRecallContextService(
            recall_repo(self.repo),
            build_authorization_filter(
                AssetIdentity(
                    "doris",
                    "analytics",
                    "orders",
                    "status",
                )
            ),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
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
        self.assertEqual(recalled.response.failures, first.failures)
        self.assertEqual(
            recalled.response.warnings,
            ["排序后的字段上下文已截断，最多保留 30 个资源"],
        )
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
        self.assertEqual(
            persisted.response.warnings,
            ["排序后的字段上下文已截断，最多保留 30 个资源"],
        )


class SemanticRecallToolTest(unittest.IsolatedAsyncioTestCase):
    """验证模型可见参数和工具运行时注入。"""

    def test_runtime_is_hidden_from_tool_call_schema(self) -> None:
        for semantic_tool in (recall_context, list_recalls):
            schema = cast(type[BaseModel], semantic_tool.tool_call_schema)
            properties = schema.model_json_schema().get(
                "properties",
                {},
            )
            self.assertNotIn("runtime", properties)
        search_schema = cast(
            type[BaseModel],
            recall_context.tool_call_schema,
        ).model_json_schema()
        search_properties = search_schema["properties"]
        self.assertEqual(
            set(search_properties),
            {"query", "resource_types", "terms", "limit_per_type"},
        )
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
        self.assertEqual(set(delete_schema.model_fields), {"deletions"})

    async def test_query_normalization_errors_are_reported_as_invalid_requests(
        self,
    ) -> None:
        cases = [
            (
                recall_context,
                {
                    "query": " ",
                    "resource_types": ["column"],
                    "terms": ["收入"],
                },
                ["query"],
                "语义召回请求无效",
            ),
            (get_recall, {"query": " "}, ["query"], "语义召回请求无效"),
            (
                merge_recalls,
                {"target_query": " ", "source_query": "收入"},
                ["target_query"],
                "语义召回请求无效",
            ),
            (
                merge_recalls,
                {"target_query": "收入", "source_query": " "},
                ["source_query"],
                "语义召回请求无效",
            ),
            (
                delete_recalls,
                {"deletions": [{"query": "收入"}, {"query": " "}]},
                ["deletions", 1, "query"],
                "删除请求无效",
            ),
        ]

        for tool, arguments, location, message in cases:
            with self.subTest(location=location):
                result = await tool.coroutine(runtime=MagicMock(), **arguments)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["message"], message)
                self.assertEqual(result["details"][0]["loc"], location)
                self.assertEqual(result["details"][0]["msg"], "query 不能为空")

    async def test_merge_same_query_reports_argument_detail(self) -> None:
        coroutine = cast(StructuredTool, merge_recalls).coroutine
        assert coroutine is not None
        result = await coroutine(
            runtime=MagicMock(),
            target_query="本月收入",
            source_query="本月收入",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "语义召回请求无效")
        self.assertEqual(
            result["details"],
            [
                {
                    "loc": ["source_query"],
                    "msg": "目标 query 和来源 query 不能相同",
                }
            ],
        )

    async def test_delete_recalls_accepts_hierarchical_resource_selectors(
        self,
    ) -> None:
        service = MagicMock()
        conversation_id = uuid4()
        experience_id = uuid4()
        service.delete = AsyncMock(return_value=[])
        runtime = SimpleNamespace(
            config={
                "configurable": {
                    "user_id": 7,
                    "conversation_id": str(conversation_id),
                }
            }
        )

        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args, **kwargs: object_context(service),
            ),
        ):
            coroutine = cast(StructuredTool, delete_recalls).coroutine
            assert coroutine is not None
            result = await coroutine(
                runtime=runtime,
                deletions=[
                    {
                        "query": " 本月收入 ",
                        "tables": {
                            "customers": {},
                            "orders": {"columns": {"status": {"values": ["paid"]}}},
                        },
                        "metrics": {"revenue": {}},
                        "query_experiences": [{"id": str(experience_id)}],
                    }
                ],
            )

        deletion = service.delete.await_args.args[2][0]
        self.assertEqual(deletion.query, "本月收入")
        self.assertTrue(deletion.tables["customers"].deletes_entire_table)
        columns = deletion.tables["orders"].columns
        assert columns is not None
        status_deletion = columns["status"]
        self.assertEqual(status_deletion.values, ["paid"])
        self.assertEqual(set(deletion.metrics), {"revenue"})
        self.assertEqual(deletion.query_experiences[0].id, experience_id)
        self.assertEqual(
            result,
            {
                "status": "success",
                "recalls": [
                    {
                        "query": "本月收入",
                        "status": "stored",
                    }
                ],
            },
        )

    async def test_delete_recalls_rejects_empty_deletions(self) -> None:
        coroutine = cast(StructuredTool, delete_recalls).coroutine
        assert coroutine is not None
        result = await coroutine(
            runtime=MagicMock(),
            deletions=[],
        )

        self.assertEqual(
            result,
            {
                "status": "error",
                "message": "删除请求无效",
                "details": [{"loc": ["deletions"], "msg": "至少需要一个删除项"}],
            },
        )

    def test_reference_loader_rejects_noncanonical_query(self) -> None:
        message = ToolMessage(
            id="message-1",
            tool_call_id="call-1",
            name="recall_context",
            content=json.dumps({"status": "stored", "query": " revenue "}),
        )

        self.assertIsNone(parse_semantic_recall_references(message))

    async def test_merged_source_queries_are_hidden_from_model_payloads(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        service = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        conversation_id = uuid4()
        for query, recall_id in (("订单金额", "recall_a"), ("本月收入", "recall_b")):
            await service.record(
                7,
                conversation_id,
                query,
                build_request(query, ["column"]),
                build_response(recall_id, query, score=0.8, reason=query),
                [],
                datetime.now(UTC),
            )
        merged = await service.merge(7, conversation_id, "本月收入", "订单金额")

        summary = _record_summary(merged)
        expanded = semantic_recall_payload(merged)

        self.assertEqual(merged.source_queries, ["订单金额"])
        self.assertEqual(
            summary,
            {
                "query": "本月收入",
                "created_at": merged.created_at.isoformat(),
                "updated_at": merged.updated_at.isoformat(),
            },
        )
        self.assertNotIn("source_queries", expanded)

    async def test_model_payload_only_contains_metadata_and_query_experiences(
        self,
    ) -> None:
        service = SemanticRecallContextService(
            recall_repo(InMemorySemanticRecallRepo()),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        response = build_response("recall_a", "本月收入", score=0.8, reason="收入")
        response.values[0].c_name = "amount"
        record = await service.record(
            7,
            uuid4(),
            "本月收入",
            build_request("本月收入", ["column"]),
            response,
            [build_query_experience()],
            datetime.now(UTC),
        )

        payload = semantic_recall_payload(record)

        self.assertEqual(
            set(payload),
            {
                "query",
                "created_at",
                "updated_at",
                "metrics",
                "tables",
                "query_experiences",
            },
        )
        self.assertEqual(
            list(payload),
            [
                "query",
                "tables",
                "metrics",
                "query_experiences",
                "created_at",
                "updated_at",
            ],
        )
        self.assertEqual(payload["query"], "本月收入")
        self.assertEqual(payload["created_at"], record.created_at.isoformat())
        self.assertEqual(payload["updated_at"], record.updated_at.isoformat())
        self.assertEqual(
            set(payload["metrics"]["revenue"]),
            {"description", "alias", "relevant_columns"},
        )
        self.assertEqual(
            set(payload["tables"]["orders"]),
            {"role", "description", "primary_key_columns", "columns"},
        )
        self.assertEqual(
            set(payload["tables"]["orders"]["columns"]["amount"]),
            {
                "type",
                "description",
                "alias",
                "examples",
                "reference_t_name",
                "reference_c_name",
                "values",
            },
        )
        self.assertEqual(
            payload["tables"]["orders"]["columns"]["amount"]["values"],
            ["paid"],
        )
        self.assertEqual(
            set(payload["query_experiences"][0]),
            {"id", "purpose", "sql_template", "assets"},
        )
        self.assertEqual(
            set(payload["query_experiences"][0]["assets"][0]),
            {"kind", "database", "table", "column"},
        )

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(
                    *(keys(item) for item in value.values())
                )
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()

        forbidden_fields = {
            "status",
            "terms",
            "rank_score",
            "match_reasons",
            "index_status",
            "meta_version",
            "index_version",
            "inclusion_reasons",
            "sync_status",
            "synced_at",
            "failures",
            "warnings",
            "truncated",
            "query_experiences_retrieved_at",
        }
        self.assertTrue(forbidden_fields.isdisjoint(keys(payload)))
        self.assertEqual(record.response.columns[0].meta_version, 1)
        self.assertEqual(record.query_experiences[0].assets[0].meta_version, 1)

    async def test_recall_context_uses_query_for_experiences_and_terms_for_resources(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        conversation_id = uuid4()
        policy = AssetAccessPolicy(
            user_id=7,
            role_name="analyst",
            authorization_fingerprint="a" * 64,
            grants=frozenset({_CONFIGURED_DATABASE_GRANT}),
        )
        response = build_response(
            "recall_a",
            "收入",
            score=0.8,
            reason="收入",
        )
        response.terms = ["收入", "订单金额"]
        experience = build_query_experience()
        resource_recall_service = MagicMock()
        second_response = response.model_copy(
            deep=True,
            update={"recall_id": "recall_b", "terms": ["订单状态"]},
        )
        second_response.columns[0].name = "status"
        second_response.columns[0].examples = ["paid"]
        third_response = response.model_copy(
            deep=True,
            update={"recall_id": "recall_c", "terms": ["今日收入"]},
        )
        resource_recall_service.recall = AsyncMock(
            side_effect=[
                response,
                second_response,
                third_response,
                RuntimeError("resource recall down"),
            ]
        )
        experience_service = MagicMock()
        experience_service.recall = AsyncMock(
            side_effect=[
                QueryExperienceRecall(status="success", results=[experience]),
                RuntimeError("experience recall down"),
            ]
        )
        runtime = SimpleNamespace(
            config={
                "configurable": {
                    "user_id": 7,
                    "conversation_id": str(conversation_id),
                }
            }
        )

        recall_runtime = SemanticRecallRuntime(
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        recall_tool = create_semantic_recall_tools(recall_runtime)[0]
        context_service = SemanticRecallContextService(
            recall_repo(repo),
            MetadataAuthorizationFilter(policy, "doris", "ecommerce"),
            query_experience_role_name=policy.role_name,
            query_experience_authorization_fingerprint=policy.authorization_fingerprint,
        )
        with (
            patch(
                "app.assistant.agents.explorer.recall_runtime.load_asset_policy",
                new=AsyncMock(return_value=policy),
            ),
            patch(
                "app.assistant.agents.explorer.recall_runtime.build_semantic_resource_recall_service",
                new=AsyncMock(return_value=resource_recall_service),
            ),
            patch(
                "app.assistant.agents.explorer.recall_runtime.build_query_experience_recall_service",
                return_value=experience_service,
            ),
            patch(
                "app.assistant.agents.explorer.recall_runtime.semantic_recall_context",
                side_effect=lambda *args: object_context(context_service),
            ),
            patch.object(
                recall_runtime.meta,
                "session",
                side_effect=lambda: object_context(MagicMock()),
            ),
        ):
            first_result = await cast(Any, recall_tool).coroutine(
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
            second_result = await cast(Any, recall_tool).coroutine(
                runtime=runtime,
                resource_types=["column", "metric"],
                query="统计本月订单收入",
                terms=["订单状态"],
            )
            third_result = await cast(Any, recall_tool).coroutine(
                runtime=runtime,
                resource_types=["column"],
                query="统计今日订单收入",
                terms=["今日收入"],
            )
            resource_error = await cast(Any, recall_tool).coroutine(
                runtime=runtime,
                resource_types=["column"],
                query="统计明日订单收入",
                terms=["明日收入"],
            )

        resource_requests = [
            call.args[0] for call in resource_recall_service.recall.await_args_list
        ]
        self.assertTrue(all(not hasattr(item, "query") for item in resource_requests))
        self.assertEqual(
            [item.terms for item in resource_requests],
            [
                ["收入", "订单金额"],
                ["订单状态"],
                ["今日收入"],
                ["明日收入"],
            ],
        )
        self.assertEqual(
            [
                call.kwargs["query"]
                for call in experience_service.recall.await_args_list
            ],
            ["统计本月订单收入", "统计今日订单收入"],
        )
        self.assertTrue(
            all(
                call.kwargs["role_name"] == "analyst"
                and call.kwargs["authorization_fingerprint"]
                == policy.authorization_fingerprint
                for call in experience_service.recall.await_args_list
            )
        )
        self.assertEqual(
            first_result,
            {"status": "stored", "query": "统计本月订单收入"},
        )
        self.assertEqual(
            second_result,
            {"status": "stored", "query": "统计本月订单收入"},
        )
        self.assertEqual(
            third_result,
            {"status": "stored", "query": "统计今日订单收入"},
        )
        self.assertEqual(
            resource_error,
            {
                "status": "error",
                "message": "语义资源召回失败",
                "details": [{"type": "RuntimeError", "msg": "resource recall down"}],
            },
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
        third_stored = await repo.get_latest_by_query(
            7,
            conversation_id,
            "统计今日订单收入",
        )
        assert third_stored is not None
        self.assertEqual(third_stored.query_experiences, [])
        self.assertEqual(
            third_stored.query_experiences_retrieved_at,
            datetime.min.replace(tzinfo=UTC),
        )

    async def test_tool_message_persists_reference_and_model_sees_authorized_record(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        service_with_full_database_grant = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        conversation_id = uuid4()
        experience = build_query_experience(column="status")
        response = build_response("recall_a", "revenue", score=0.8, reason="query")
        response.columns.append(
            response.columns[0].model_copy(
                update={
                    "name": "status",
                    "type": "string",
                    "description": "订单状态",
                    "alias": ["状态"],
                    "examples": ["paid"],
                }
            )
        )
        record = await service_with_full_database_grant.record(
            7,
            conversation_id,
            "revenue",
            build_request("revenue", ["column"]),
            response,
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
            name="recall_context",
            content=reference_content,
        )
        current_reference = ToolMessage(
            id="current_message",
            tool_call_id="current_call",
            name="recall_context",
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
        restricted_service = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(
                AssetIdentity("doris", "analytics", "orders", "status")
            ),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        seen_messages: list[object] = []

        async def handler(model_request: ModelRequest[Any]) -> ModelResponse[Any]:
            seen_messages.extend(model_request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        with (
            patch(
                "app.assistant.agents.middleware.semantic_recall_expansion.get_config",
                return_value={
                    "configurable": {
                        "user_id": 7,
                        "conversation_id": str(conversation_id),
                    }
                },
            ),
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args, **kwargs: object_context(restricted_service),
            ),
        ):
            await SemanticRecallExpansionMiddleware(recall=_RECALL).awrap_model_call(
                request,
                handler,
            )
            display_messages = await expand_semantic_recall_messages_for_display(
                [current_reference], 7, conversation_id, recall=_RECALL
            )

        self.assertEqual(current_reference.content, reference_content)
        self.assertNotIn("recall_id", reference_content)
        self.assertNotIn("amount", reference_content)
        self.assertNotIn("SELECT status", reference_content)
        self.assertEqual(getattr(seen_messages[0], "content", None), reference_content)
        expanded_content = str(getattr(seen_messages[2], "content", ""))
        self.assertNotIn("recall_id", expanded_content)
        self.assertNotIn("amount", expanded_content)
        self.assertIn("paid", expanded_content)
        self.assertIn("SELECT status", expanded_content)
        display_content = str(getattr(display_messages[0], "content", ""))
        self.assertIn("paid", display_content)
        self.assertIn("SELECT status", display_content)

    async def test_missing_reference_does_not_hide_successful_current_turn_recall(
        self,
    ) -> None:
        repo = InMemorySemanticRecallRepo()
        conversation_id = uuid4()
        service = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        current_record = await service.record(
            7,
            conversation_id,
            "当前有效问题",
            build_request("收入", ["column"]),
            build_response("recall_current", "收入", score=0.8, reason="收入"),
            [],
            datetime.now(UTC),
        )
        missing_reference = ToolMessage(
            tool_call_id="missing_call",
            name="recall_context",
            content=json.dumps({"status": "stored", "query": "已删除问题"}),
        )
        current_reference = ToolMessage(
            tool_call_id="current_call",
            name="recall_context",
            content=json.dumps(semantic_recall_reference(current_record)),
        )
        messages = [
            HumanMessage(content="继续探索"),
            missing_reference,
            current_reference,
        ]
        request = ModelRequest(
            model=GenericFakeChatModel(messages=iter([AIMessage(content="ok")])),
            messages=messages,
            runtime=Runtime(),
        )
        seen_messages: list[object] = []

        async def handler(model_request: ModelRequest[Any]) -> ModelResponse[Any]:
            seen_messages.extend(model_request.messages)
            return ModelResponse(result=[AIMessage(content="ok")])

        with (
            patch(
                "app.assistant.agents.middleware.semantic_recall_expansion.get_config",
                return_value={
                    "configurable": {
                        "user_id": 7,
                        "conversation_id": str(conversation_id),
                    }
                },
            ),
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args, **kwargs: object_context(service),
            ),
        ):
            await SemanticRecallExpansionMiddleware(recall=_RECALL).awrap_model_call(
                request,
                handler,
            )
            display_messages = await expand_semantic_recall_messages_for_display(
                [missing_reference, current_reference],
                7,
                conversation_id,
                recall=_RECALL,
            )

        model_missing = json.loads(str(getattr(seen_messages[1], "content", "")))
        model_current = json.loads(str(getattr(seen_messages[2], "content", "")))
        self.assertEqual(model_missing["status"], "error")
        self.assertEqual(model_missing["queries"], ["已删除问题"])
        self.assertEqual(model_current["query"], "当前有效问题")
        self.assertIn("orders", model_current["tables"])

        display_missing = json.loads(str(display_messages[0].content))
        display_current = json.loads(str(display_messages[1].content))
        self.assertEqual(display_missing["queries"], ["已删除问题"])
        self.assertEqual(display_current["query"], "当前有效问题")

    async def test_get_tool_writes_only_recall_reference_to_state(self) -> None:
        repo = InMemorySemanticRecallRepo()
        service = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        conversation_id = uuid4()
        await service.record(
            1,
            conversation_id,
            "revenue",
            build_request("revenue", ["column"]),
            build_response("recall_a", "revenue", score=0.8, reason="query"),
            [],
            datetime.now(UTC),
        )
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([get_recall]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args, **kwargs: object_context(service),
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

        service = SemanticRecallContextService(
            recall_repo(repo),
            build_authorization_filter(_FULL_DATABASE_GRANT),
            query_experience_role_name=None,
            query_experience_authorization_fingerprint=None,
        )
        with (
            patch.object(
                _RECALL,
                "context_service",
                side_effect=lambda *args, **kwargs: object_context(service),
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


class SemanticRecallRequestTest(unittest.TestCase):
    def test_resource_terms_require_nonempty_normalized_value(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticResourceRecallRequest(
                terms=["", "  "],
                resource_types=["column"],
            )


if __name__ == "__main__":
    unittest.main()
