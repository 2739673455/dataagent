"""查询经验记录和检索测试。"""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData

from app.identity.models.doris import asset_resource_key
from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.search import SearchHit
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecution,
    QueryResultColumn,
)
from app.query.models.experience import (
    QUERY_EXPERIENCE_PURPOSE_LIMIT,
    QueryExperience,
    QueryExperienceAsset,
)
from app.query.models.validation import (
    QueryColumnRef,
    QueryTableRef,
    QueryValidationResult,
)
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.executor import (
    QueryPlanEstimate,
    SuccessfulQueryExecution,
)
from app.query.services.experience import (
    QueryExecutionContext,
    QueryExperienceService,
    _build_sql_template,
)
from app.query.services.experience_invalidation import (
    QueryExperienceInvalidationService,
)
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.contracts.analysis import AgentSessionKey
from tests.identity.test_auth_service import AsyncSessionStub

AUTHORIZATION_EPOCH = uuid4()


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.texts: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts.append(texts)
        return [[0.1, 0.2] for _ in texts]

    async def aclose(self) -> None:
        return None


class FakeIndexRepo:
    def __init__(self) -> None:
        self.indexed: list[dict[str, object]] = []
        self.text_hits: list[SearchHit[UUID]] = []
        self.vector_hits: list[SearchHit[UUID]] = []
        self.text_error: Exception | None = None
        self.vector_error: Exception | None = None
        self.index_error: Exception | None = None
        self.deleted: list[tuple[UUID, int]] = []
        self.delete_error: Exception | None = None

    async def index(
        self,
        experience_id: UUID,
        *,
        revision: int,
        role_name: str,
        authorization_epoch: UUID,
        text: str,
        embedding: list[float],
    ) -> None:
        if self.index_error is not None:
            raise self.index_error
        self.indexed.append(
            {
                "experience_id": experience_id,
                "revision": revision,
                "role_name": role_name,
                "authorization_epoch": authorization_epoch,
                "text": text,
                "embedding": embedding,
            }
        )

    async def delete(self, experience_id: UUID, *, revision: int) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((experience_id, revision))

    async def search_text(
        self,
        query: str,
        *,
        role_name: str,
        authorization_epoch: UUID,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        del query, role_name, authorization_epoch, limit
        if self.text_error is not None:
            raise self.text_error
        return self.text_hits

    async def search_vector(
        self,
        embedding: list[float],
        *,
        role_name: str,
        authorization_epoch: UUID,
        limit: int,
        min_score: float,
    ) -> list[SearchHit[UUID]]:
        del embedding, role_name, authorization_epoch, limit, min_score
        if self.vector_error is not None:
            raise self.vector_error
        return self.vector_hits


class FakeIndexScheduler:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, int]] = []

    def enqueue(self, experience_id: UUID, revision: int) -> None:
        self.enqueued.append((experience_id, revision))


class FakePGRepo:
    def __init__(self) -> None:
        self.session = AsyncSessionStub()
        self.executions: list[QueryExecution] = []
        self.experiences: list[QueryExperience] = []
        self.marked: list[tuple[UUID, int]] = []
        self.current_versions: dict[str, int] = {}

    async def metadata_versions(
        self,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
    ) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        return (
            {table_name: 3 for table_name in table_names},
            {column_key: 5 for column_key in column_keys},
        )

    async def record_success(
        self,
        execution: QueryExecution,
        experience: QueryExperience,
        assets: list[QueryExperienceAsset],
    ) -> QueryExperience:
        execution.experience_id = experience.id
        experience.assets = assets
        experience.status = "active"
        experience.revision = 1
        experience.indexed_revision = 0
        self.executions.append(execution)
        stored = next(
            (
                item
                for item in self.experiences
                if item.role_name == experience.role_name
                and item.fingerprint == experience.fingerprint
            ),
            None,
        )
        if stored is None:
            self.experiences.append(experience)
            return experience
        update_assets = stored.refresh_from_success(
            purpose=experience.purposes[0],
            authorization_epoch=experience.authorization_epoch,
            sql_template=experience.sql_template,
        )
        if update_assets:
            stored.assets = assets
        execution.experience_id = stored.id
        return stored

    async def record_failure(self, execution: QueryExecution) -> None:
        self.executions.append(execution)

    async def record_execution(self, execution: QueryExecution) -> None:
        self.executions.append(execution)

    async def get(self, experience_id: UUID) -> QueryExperience | None:
        return next(
            (item for item in self.experiences if item.id == experience_id),
            None,
        )

    async def mark_indexes_synced(self, revisions: dict[UUID, int]) -> None:
        self.marked.extend(revisions.items())
        for experience in self.experiences:
            revision = revisions.get(experience.id)
            if revision is not None and revision == experience.revision:
                experience.indexed_revision = revision

    async def list_pending_index_repairs(self, *, limit: int) -> dict[UUID, int]:
        return dict(
            list(
                {
                    item.id: item.revision
                    for item in self.experiences
                    if item.indexed_revision < item.revision
                }.items()
            )[:limit]
        )

    async def disable_for_changed_resources(
        self,
        resource_keys: set[str],
    ) -> dict[UUID, int]:
        experience_ids = {
            item.id
            for item in self.experiences
            if item.status == "active"
            and any(asset.resource_key in resource_keys for asset in item.assets)
        }
        return await self.disable_for_metadata_change(experience_ids)

    async def disable_for_metadata_change(
        self,
        experience_ids: set[UUID] | list[UUID],
    ) -> dict[UUID, int]:
        revisions: dict[UUID, int] = {}
        for experience in self.experiences:
            if experience.id not in experience_ids or experience.status != "active":
                continue
            experience.status = "disabled"
            experience.disabled_reason = "metadata_changed"
            experience.disabled_at = datetime.now(UTC)
            experience.revision += 1
            revisions[experience.id] = experience.revision
        return revisions

    async def finalize_deletion(self, experience_id: UUID, revision: int) -> bool:
        for experience in self.experiences:
            if (
                experience.id == experience_id
                and experience.revision == revision
                and experience.status == "deleting"
            ):
                self.experiences.remove(experience)
                return True
        return False

    async def get_many(
        self,
        experience_ids: list[UUID],
        *,
        role_name: str,
        authorization_epoch: UUID,
    ) -> list[QueryExperience]:
        by_id = {
            item.id: item
            for item in self.experiences
            if item.role_name == role_name
            and item.authorization_epoch == authorization_epoch
        }
        return [by_id[item_id] for item_id in experience_ids if item_id in by_id]

    async def current_asset_versions(
        self,
        experiences: list[QueryExperience],
    ) -> dict[str, int]:
        del experiences
        return self.current_versions


def build_service(
    repo: FakePGRepo,
    index_repo: FakeIndexRepo,
    embedding_client: FakeEmbeddingClient,
    scheduler: FakeIndexScheduler | None = None,
) -> QueryExperienceService:
    return QueryExperienceService(
        repo=cast(QueryExperiencePGRepo, repo),
        index_repo=cast(QueryExperienceESRepo, index_repo),
        embedding_client=cast(EmbeddingClient, embedding_client),
        index_scheduler=scheduler or FakeIndexScheduler(),
        data_source="doris",
        database_name="analytics",
    )


def build_invalidation_service(
    repo: FakePGRepo,
    scheduler: FakeIndexScheduler | None = None,
) -> QueryExperienceInvalidationService:
    return QueryExperienceInvalidationService(
        repo=cast(QueryExperiencePGRepo, repo),
        index_scheduler=scheduler or FakeIndexScheduler(),
        data_source="doris",
        database_name="analytics",
    )


def build_experience(
    *,
    table_name: str,
    column_name: str,
    meta_version: int,
    status: str = "active",
    role_name: str = "analyst",
    authorization_epoch: UUID = AUTHORIZATION_EPOCH,
) -> QueryExperience:
    experience_id = uuid4()
    table_key = asset_resource_key("doris", "analytics", table_name)
    column_key = asset_resource_key(
        "doris",
        "analytics",
        table_name,
        column_name,
    )
    experience = QueryExperience(
        id=experience_id,
        role_name=role_name,
        authorization_epoch=authorization_epoch,
        fingerprint=uuid4().hex,
        purposes=["统计订单金额"],
        sql_template=f"SELECT {column_name} FROM {table_name} WHERE id = :p1",
        status=status,
        disabled_reason="metadata_changed" if status == "disabled" else None,
        disabled_at=datetime.now(UTC) if status == "disabled" else None,
        revision=2,
        indexed_revision=2,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    experience.assets = [
        QueryExperienceAsset(
            experience_id=experience_id,
            kind="table",
            resource_key=table_key,
            data_source="doris",
            database_name="analytics",
            table_name=table_name,
            meta_version=meta_version,
        ),
        QueryExperienceAsset(
            experience_id=experience_id,
            kind="column",
            resource_key=column_key,
            data_source="doris",
            database_name="analytics",
            table_name=table_name,
            column_name=column_name,
            meta_version=meta_version,
        ),
    ]
    return experience


class QueryExperienceServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_successful_reexecution_restores_disabled_experience(self) -> None:
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            status="disabled",
        )
        previous_revision = experience.revision

        experience.refresh_from_success(
            purpose="按地区统计订单金额",
            authorization_epoch=experience.authorization_epoch,
            sql_template="SELECT region, SUM(amount) FROM orders GROUP BY region",
        )

        self.assertEqual(experience.status, "active")
        self.assertEqual(experience.revision, previous_revision + 1)

    def test_successful_reexecution_preserves_admin_disabled_experience(self) -> None:
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            status="disabled",
        )
        experience.disabled_reason = "admin"
        experience.disabled_by_user_id = 9

        experience.refresh_from_success(
            purpose="按地区统计订单金额",
            authorization_epoch=experience.authorization_epoch,
            sql_template="SELECT region, SUM(amount) FROM orders GROUP BY region",
        )

        self.assertEqual(experience.status, "disabled")
        self.assertEqual(experience.disabled_reason, "admin")
        self.assertEqual(experience.disabled_by_user_id, 9)

    def test_successful_reexecution_does_not_update_deleting_experience(self) -> None:
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            status="deleting",
        )
        experience.disabled_reason = None
        experience.disabled_at = None
        experience.deletion_requested_by_user_id = 9
        experience.deletion_requested_at = datetime.now(UTC)
        previous_revision = experience.revision
        previous_purposes = list(experience.purposes)

        changed = experience.refresh_from_success(
            purpose="按地区统计订单金额",
            authorization_epoch=experience.authorization_epoch,
            sql_template="SELECT region, SUM(amount) FROM orders GROUP BY region",
        )

        self.assertFalse(changed)
        self.assertEqual(experience.revision, previous_revision)
        self.assertEqual(experience.purposes, previous_purposes)

    def test_new_authorization_epoch_resets_shared_purposes(self) -> None:
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        next_epoch = uuid4()

        experience.refresh_from_success(
            purpose="按渠道统计订单金额",
            authorization_epoch=next_epoch,
            sql_template=experience.sql_template,
        )

        self.assertEqual(experience.authorization_epoch, next_epoch)
        self.assertEqual(experience.purposes, ["按渠道统计订单金额"])

    def test_successful_reexecution_keeps_only_semantic_retrieval_purposes(
        self,
    ) -> None:
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )

        for index in range(QUERY_EXPERIENCE_PURPOSE_LIMIT + 1):
            experience.refresh_from_success(
                purpose=f"查询目的 {index}",
                authorization_epoch=experience.authorization_epoch,
                sql_template=experience.sql_template,
            )

        self.assertEqual(
            experience.purposes,
            [
                f"查询目的 {index}"
                for index in range(1, QUERY_EXPERIENCE_PURPOSE_LIMIT + 1)
            ],
        )

    def test_sql_template_redacts_literals_and_has_stable_fingerprint(self) -> None:
        first_template, first_fingerprint = _build_sql_template(
            "SELECT amount FROM orders WHERE region = '华东' AND day >= '2026-01-01'"
        )
        second_template, second_fingerprint = _build_sql_template(
            "SELECT amount FROM orders WHERE region = '华南' AND day >= '2026-08-01'"
        )

        self.assertEqual(first_template, second_template)
        self.assertEqual(first_fingerprint, second_fingerprint)
        self.assertEqual(first_template.count(":p"), 2)
        self.assertNotIn("华东", first_template)
        self.assertNotIn("2026-01-01", first_template)

    async def test_success_records_lineage_and_indexes_only_safe_text(self) -> None:
        repo = FakePGRepo()
        index_repo = FakeIndexRepo()
        embedding_client = FakeEmbeddingClient()
        scheduler = FakeIndexScheduler()
        service = build_service(repo, index_repo, embedding_client, scheduler)
        session_key = AgentSessionKey(
            user_id=7,
            conversation_id=uuid4(),
            analysis_id="sales",
            agent_type="explorer",
            session_id="orders",
        )
        details = SuccessfulQueryExecution(
            session_key=session_key,
            raw_sql="SELECT amount FROM orders WHERE region = '华东'",
            normalized_sql="SELECT amount FROM orders WHERE region = '华东' LIMIT 100",
            validation=QueryValidationResult(
                valid=True,
                normalized_sql=(
                    "SELECT amount FROM orders WHERE region = '华东' LIMIT 100"
                ),
                tables=[QueryTableRef(database="analytics", name="orders")],
                columns=[
                    QueryColumnRef(
                        database="analytics",
                        table="orders",
                        name="amount",
                    ),
                    QueryColumnRef(
                        database="analytics",
                        table="orders",
                        name="region",
                    ),
                ],
                output_columns=["amount"],
            ),
            plan_estimate=QueryPlanEstimate(
                scan_nodes=1,
                scan_rows=100,
                scan_bytes=800,
            ),
            result=AnalysisQueryResult(
                path="/sessions/sales/explorer/orders/query.csv",
                columns=[
                    QueryResultColumn(name="amount", type="integer", nullable=False)
                ],
                row_count=3,
                time_range={},
                sample=[{"amount": 42}],
            ),
        )

        experience_id = await service.record_success(
            QueryExecutionContext(
                session_key=details.session_key,
                role_name="analyst",
                authorization_epoch=AUTHORIZATION_EPOCH,
                purpose="统计华东订单金额",
                tool_call_id="call-1",
            ),
            details,
        )

        execution = repo.executions[0]
        self.assertEqual(execution.experience_id, experience_id)
        self.assertEqual(execution.status, "succeeded")
        result_summary = execution.result_summary
        assert result_summary is not None
        self.assertEqual(result_summary["row_count"], 3)
        self.assertNotIn("sample", result_summary)
        assets = repo.experiences[0].assets
        self.assertEqual(
            {(item.kind, item.table_name, item.column_name) for item in assets},
            {
                ("table", "orders", None),
                ("column", "orders", "amount"),
                ("column", "orders", "region"),
            },
        )
        self.assertEqual(
            {(item.kind, item.meta_version) for item in assets},
            {("table", 3), ("column", 5)},
        )
        self.assertEqual(scheduler.enqueued, [(experience_id, 1)])
        self.assertEqual(index_repo.indexed, [])
        self.assertEqual(embedding_client.texts, [])

    async def test_catalog_success_is_audited_without_creating_experience(self) -> None:
        repo = FakePGRepo()
        scheduler = FakeIndexScheduler()
        service = build_service(
            repo,
            FakeIndexRepo(),
            FakeEmbeddingClient(),
            scheduler,
        )
        session_key = AgentSessionKey(
            user_id=7,
            conversation_id=uuid4(),
            analysis_id="sales",
            agent_type="explorer",
            session_id="discovery",
        )
        details = SuccessfulQueryExecution(
            session_key=session_key,
            raw_sql="SHOW TABLES",
            normalized_sql="SHOW TABLES",
            validation=QueryValidationResult(
                valid=True,
                normalized_sql="SHOW TABLES",
                query_kind="catalog",
            ),
            plan_estimate=None,
            result=AnalysisQueryResult(
                path="/sessions/sales/explorer/discovery/query.csv",
                columns=[
                    QueryResultColumn(
                        name="Tables_in_analytics",
                        type="string",
                        nullable=False,
                    )
                ],
                row_count=1,
                time_range={},
                sample=[{"Tables_in_analytics": "orders"}],
            ),
        )

        experience_id = await service.record_success(
            QueryExecutionContext(
                session_key=session_key,
                role_name="analyst",
                authorization_epoch=AUTHORIZATION_EPOCH,
                purpose="查看可用表",
                tool_call_id="call-catalog",
            ),
            details,
        )

        self.assertIsNone(experience_id)
        self.assertEqual(len(repo.executions), 1)
        self.assertIsNone(repo.executions[0].experience_id)
        validation = repo.executions[0].validation
        assert validation is not None
        self.assertEqual(
            validation["query_kind"],
            "catalog",
        )
        self.assertEqual(repo.experiences, [])
        self.assertEqual(scheduler.enqueued, [])

    async def test_recall_disables_changed_assets_and_filters_latest_permissions(
        self,
    ) -> None:
        repo = FakePGRepo()
        index_repo = FakeIndexRepo()
        embedding_client = FakeEmbeddingClient()
        allowed = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        denied = build_experience(
            table_name="salaries",
            column_name="amount",
            meta_version=1,
        )
        stale = build_experience(
            table_name="orders",
            column_name="region",
            meta_version=1,
        )
        same_role = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        same_role.purposes = ["同角色成员沉淀的订单查询"]
        other_role = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            role_name="finance",
        )
        old_epoch = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            authorization_epoch=uuid4(),
        )
        repo.experiences = [allowed, denied, stale, same_role, other_role, old_epoch]
        repo.current_versions = {asset.resource_key: 1 for asset in allowed.assets}
        repo.current_versions.update({asset.resource_key: 1 for asset in denied.assets})
        repo.current_versions.update(
            {
                asset.resource_key: 2 if asset.kind == "column" else 1
                for asset in stale.assets
            }
        )
        index_repo.text_hits = [
            SearchHit(item=other_role.id, score=30),
            SearchHit(item=old_epoch.id, score=25),
            SearchHit(item=same_role.id, score=20),
            SearchHit(item=denied.id, score=10),
            SearchHit(item=stale.id, score=8),
            SearchHit(item=allowed.id, score=5),
        ]
        index_repo.vector_hits = [SearchHit(item=allowed.id, score=0.9)]
        scheduler = FakeIndexScheduler()
        service = build_service(repo, index_repo, embedding_client, scheduler)
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                    )
                }
            ),
        )

        recall = await service.recall(
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            policy=policy,
            query="订单金额",
            limit=5,
        )

        self.assertEqual(
            [item.sql_template for item in recall.results],
            [same_role.sql_template, allowed.sql_template],
        )
        self.assertEqual(recall.status, "success")
        self.assertEqual(stale.status, "disabled")
        self.assertEqual(scheduler.enqueued, [(stale.id, stale.revision)])

    async def test_recall_uses_rrf_order_and_returns_compact_results(self) -> None:
        repo = FakePGRepo()
        first = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        second = build_experience(
            table_name="customers",
            column_name="name",
            meta_version=1,
        )
        repo.experiences = [first, second]
        repo.current_versions = {
            asset.resource_key: 1
            for experience in repo.experiences
            for asset in experience.assets
        }
        index_repo = FakeIndexRepo()
        index_repo.text_hits = [
            SearchHit(item=first.id, score=0.1),
            SearchHit(item=second.id, score=100),
        ]
        index_repo.vector_hits = [
            SearchHit(item=first.id, score=0.1),
            SearchHit(item=second.id, score=100),
        ]
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        recall = await service.recall(
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            policy=AssetAccessPolicy(
                user_id=7,
                grants=frozenset({AssetIdentity("doris", "analytics")}),
            ),
            query="订单与客户",
            limit=2,
        )

        self.assertEqual(
            [item.sql_template for item in recall.results],
            [first.sql_template, second.sql_template],
        )
        self.assertEqual(
            set(recall.results[0].model_dump()),
            {"id", "purpose", "sql_template", "assets"},
        )

    async def test_recall_keeps_vector_results_when_text_search_fails(self) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        repo.experiences = [experience]
        repo.current_versions = {asset.resource_key: 1 for asset in experience.assets}
        index_repo = FakeIndexRepo()
        index_repo.text_error = RuntimeError("text index unavailable")
        index_repo.vector_hits = [SearchHit(item=experience.id, score=0.9)]
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        recall = await service.recall(
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            policy=AssetAccessPolicy(
                user_id=7,
                grants=frozenset({AssetIdentity("doris", "analytics")}),
            ),
            query="订单金额",
            limit=3,
        )

        self.assertEqual(recall.status, "partial")
        self.assertEqual(
            [item.sql_template for item in recall.results], [experience.sql_template]
        )

    async def test_recall_reports_failed_when_all_search_channels_fail(self) -> None:
        index_repo = FakeIndexRepo()
        index_repo.text_error = RuntimeError("text index unavailable")
        index_repo.vector_error = RuntimeError("vector index unavailable")
        service = build_service(FakePGRepo(), index_repo, FakeEmbeddingClient())

        recall = await service.recall(
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            policy=AssetAccessPolicy(
                user_id=7,
                grants=frozenset({AssetIdentity("doris", "analytics")}),
            ),
            query="订单金额",
            limit=3,
        )

        self.assertEqual(recall.status, "failed")
        self.assertEqual(recall.results, [])

    async def test_failure_records_error_without_creating_experience(self) -> None:
        repo = FakePGRepo()
        service = build_service(repo, FakeIndexRepo(), FakeEmbeddingClient())
        session_key = AgentSessionKey(
            user_id=7,
            conversation_id=uuid4(),
            analysis_id="sales",
            agent_type="explorer",
            session_id="orders",
        )

        await service.record_failure(
            QueryExecutionContext(
                session_key=session_key,
                role_name="analyst",
                authorization_epoch=AUTHORIZATION_EPOCH,
                purpose="删除订单",
            ),
            raw_sql="DELETE FROM orders",
            status="rejected",
            error_code="sql_validation_failed",
            error_detail="readonly query required",
        )

        self.assertEqual(len(repo.executions), 1)
        self.assertEqual(repo.executions[0].status, "rejected")
        self.assertIsNone(repo.executions[0].experience_id)
        self.assertEqual(repo.experiences, [])

    async def test_metadata_change_proactively_disables_matching_experiences(
        self,
    ) -> None:
        repo = FakePGRepo()
        invalid = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        retained = build_experience(
            table_name="customers",
            column_name="name",
            meta_version=1,
        )
        repo.experiences = [invalid, retained]
        scheduler = FakeIndexScheduler()
        service = build_invalidation_service(repo, scheduler)

        invalidated_ids = await service.invalidate_assets(
            table_names={"orders"},
            column_keys=set(),
        )

        self.assertEqual(invalidated_ids, [invalid.id])
        self.assertEqual(invalid.status, "disabled")
        self.assertEqual(retained.status, "active")
        self.assertEqual(scheduler.enqueued, [(invalid.id, invalid.revision)])

    async def test_sync_index_deletes_disabled_experience(self) -> None:
        repo = FakePGRepo()
        invalid = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        repo.experiences = [invalid]
        index_repo = FakeIndexRepo()
        service = build_service(repo, index_repo, FakeEmbeddingClient())
        invalidator = build_invalidation_service(repo)

        await invalidator.invalidate_assets(
            table_names={"orders"},
            column_keys=set(),
        )
        self.assertLess(invalid.indexed_revision, invalid.revision)

        await service.sync_index(invalid.id, invalid.revision)

        self.assertEqual(index_repo.deleted, [(invalid.id, invalid.revision)])
        self.assertEqual(invalid.indexed_revision, invalid.revision)

    async def test_sync_index_deletes_deleting_experience_from_both_stores(
        self,
    ) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            status="deleting",
        )
        experience.disabled_reason = None
        experience.disabled_at = None
        experience.deletion_requested_by_user_id = 9
        experience.deletion_requested_at = datetime.now(UTC)
        experience.indexed_revision = experience.revision - 1
        repo.experiences = [experience]
        index_repo = FakeIndexRepo()
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        await service.sync_index(experience.id, experience.revision)

        self.assertEqual(index_repo.deleted, [(experience.id, experience.revision)])
        self.assertEqual(repo.experiences, [])

    async def test_sync_index_writes_current_revision(self) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        experience.indexed_revision = experience.revision - 1
        repo.experiences = [experience]
        index_repo = FakeIndexRepo()
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        await service.sync_index(experience.id, experience.revision)

        self.assertEqual(index_repo.indexed[0]["revision"], experience.revision)
        self.assertEqual(index_repo.indexed[0]["text"], "统计订单金额")
        self.assertNotIn("orders", cast(str, index_repo.indexed[0]["text"]))
        self.assertNotIn("amount", cast(str, index_repo.indexed[0]["text"]))
        self.assertEqual(experience.indexed_revision, experience.revision)

    async def test_sync_failure_does_not_advance_indexed_revision(self) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        experience.indexed_revision = experience.revision - 1
        repo.experiences = [experience]
        index_repo = FakeIndexRepo()
        index_repo.index_error = RuntimeError("index unavailable")
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.sync_index(experience.id, experience.revision)

        self.assertEqual(
            experience.indexed_revision,
            experience.revision - 1,
        )
        self.assertEqual(repo.marked, [])

    async def test_delete_failure_does_not_advance_indexed_revision(self) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        repo.experiences = [experience]
        index_repo = FakeIndexRepo()
        index_repo.delete_error = RuntimeError("index unavailable")
        service = build_service(repo, index_repo, FakeEmbeddingClient())
        invalidator = build_invalidation_service(repo)
        await invalidator.invalidate_assets(
            table_names={"orders"},
            column_keys=set(),
        )

        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.sync_index(experience.id, experience.revision)

        self.assertLess(experience.indexed_revision, experience.revision)
        self.assertEqual(repo.marked, [])

    async def test_deleting_experience_is_repairable_when_index_delete_fails(
        self,
    ) -> None:
        repo = FakePGRepo()
        experience = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            status="deleting",
        )
        experience.disabled_reason = None
        experience.disabled_at = None
        experience.deletion_requested_by_user_id = 9
        experience.deletion_requested_at = datetime.now(UTC)
        experience.indexed_revision = experience.revision - 1
        repo.experiences = [experience]
        index_repo = FakeIndexRepo()
        index_repo.delete_error = RuntimeError("index unavailable")
        service = build_service(repo, index_repo, FakeEmbeddingClient())

        with self.assertRaisesRegex(RuntimeError, "index unavailable"):
            await service.sync_index(experience.id, experience.revision)

        self.assertEqual(repo.experiences, [experience])
        async with repo.session.begin():
            pending = await repo.list_pending_index_repairs(limit=10)
        self.assertEqual(pending, {experience.id: experience.revision})


class QueryExperienceESRepoTest(unittest.IsolatedAsyncioTestCase):
    async def test_index_uses_external_revision(self) -> None:
        experience_id = uuid4()
        client = MagicMock()
        client.indices.exists = AsyncMock(return_value=True)
        client.index = AsyncMock()
        repo = QueryExperienceESRepo(cast(Any, client))

        await repo.index(
            experience_id,
            revision=7,
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            text="统计订单金额",
            embedding=[0.1, 0.2],
        )

        client.index.assert_awaited_once_with(
            index=repo._index_name,
            id=str(experience_id),
            document={
                "role_name": "analyst",
                "authorization_epoch": str(AUTHORIZATION_EPOCH),
                "text": "统计订单金额",
                "embedding": [0.1, 0.2],
            },
            version=7,
            version_type="external_gte",
            refresh="wait_for",
        )

    async def test_delete_uses_external_revision(self) -> None:
        experience_id = uuid4()
        client = MagicMock()
        client.indices.exists = AsyncMock(return_value=True)
        client.delete = AsyncMock()
        repo = QueryExperienceESRepo(cast(Any, client))

        await repo.delete(experience_id, revision=8)

        client.delete.assert_awaited_once_with(
            index=repo._index_name,
            id=str(experience_id),
            version=8,
            version_type="external_gte",
            refresh="wait_for",
        )

    async def test_semantic_search_is_scoped_to_role_and_authorization_epoch(
        self,
    ) -> None:
        experience_id = uuid4()
        client = MagicMock()
        client.indices.exists = AsyncMock(return_value=True)
        client.search = AsyncMock(
            return_value=SimpleNamespace(
                body={
                    "hits": {
                        "hits": [
                            {"_id": str(experience_id), "_score": 0.8},
                            {"_id": "invalid", "_score": 1},
                        ]
                    }
                }
            )
        )
        repo = QueryExperienceESRepo(cast(Any, client))

        hits = await repo.search_vector(
            [0.1, 0.2],
            role_name="analyst",
            authorization_epoch=AUTHORIZATION_EPOCH,
            limit=5,
            min_score=0.65,
        )

        self.assertEqual([hit.item for hit in hits], [experience_id])
        request = client.search.await_args.kwargs
        self.assertEqual(request["size"], 5)
        self.assertEqual(request["min_score"], 0.65)
        filters = request["knn"]["filter"]["bool"]["filter"]
        self.assertEqual(
            filters,
            [
                {"term": {"role_name": "analyst"}},
                {"term": {"authorization_epoch": str(AUTHORIZATION_EPOCH)}},
            ],
        )


class QueryExperiencePGRepoTest(unittest.IsolatedAsyncioTestCase):
    async def test_disable_for_metadata_change_converts_returned_rows(self) -> None:
        experience_id = uuid4()
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=IteratorResult(
                SimpleResultMetaData(["id", "revision"]),
                iter([(experience_id, 3)]),
            )
        )
        session.flush = AsyncMock()
        repo = QueryExperiencePGRepo(cast(Any, session))

        revisions = await repo.disable_for_metadata_change({experience_id})

        self.assertEqual(revisions, {experience_id: 3})

    async def test_list_pending_index_repairs_converts_result_rows(self) -> None:
        experience_id = uuid4()
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=IteratorResult(
                SimpleResultMetaData(["id", "revision"]),
                iter([(experience_id, 4)]),
            )
        )
        repo = QueryExperiencePGRepo(cast(Any, session))

        revisions = await repo.list_pending_index_repairs(limit=10)

        self.assertEqual(revisions, {experience_id: 4})

    async def test_list_search_decodes_purposes_and_escapes_wildcards(self) -> None:
        session = MagicMock()
        session.scalar = AsyncMock(return_value=0)
        result = MagicMock()
        result.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        repo = QueryExperiencePGRepo(cast(Any, session))

        await repo.list_overviews(
            limit=20,
            offset=0,
            role_name=None,
            status=None,
            query="类目_%",
        )

        statement = session.scalar.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("json_array_elements_text(query_experiences.purposes)", sql)
        self.assertNotIn("CAST(query_experiences.purposes AS TEXT)", sql)
        self.assertEqual(
            sum(value == "类目/_/%" for value in compiled.params.values()),
            3,
        )

    async def test_list_hides_deleting_experiences_by_default(self) -> None:
        session = MagicMock()
        session.scalar = AsyncMock(return_value=0)
        result = MagicMock()
        result.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        repo = QueryExperiencePGRepo(cast(Any, session))

        await repo.list_overviews(
            limit=20,
            offset=0,
            role_name=None,
            status=None,
            query=None,
        )

        statement = session.scalar.await_args.args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("query_experiences.status !=", str(compiled))
        self.assertIn("deleting", compiled.params.values())


if __name__ == "__main__":
    unittest.main()
