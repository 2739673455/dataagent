"""查询经验记录和检索测试"""

import unittest
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from app.identity.models.doris import asset_resource_key
from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.search import SearchHit
from app.query.models.execution import (
    AnalysisQueryResult,
    QueryExecution,
    QueryResultColumn,
)
from app.query.models.experience import (
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
    build_sql_template,
)
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.contracts.analysis import AgentSessionKey
from tests.identity.test_auth_service import AsyncSessionStub


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
        self.deleted: list[UUID] = []
        self.delete_error: Exception | None = None

    async def index(
        self,
        experience_id: UUID,
        *,
        owner_user_id: int,
        role_name: str,
        quality: str,
        text: str,
        embedding: list[float],
    ) -> None:
        self.indexed.append(
            {
                "experience_id": experience_id,
                "owner_user_id": owner_user_id,
                "role_name": role_name,
                "quality": quality,
                "text": text,
                "embedding": embedding,
            }
        )

    async def delete_many(self, experience_ids: list[UUID]) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.extend(experience_ids)

    async def search_text(
        self,
        query: str,
        *,
        user_id: int,
        role_name: str,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        del query, user_id, role_name, limit
        return self.text_hits

    async def search_vector(
        self,
        embedding: list[float],
        *,
        user_id: int,
        role_name: str,
        limit: int,
    ) -> list[SearchHit[UUID]]:
        del embedding, user_id, role_name, limit
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
        experience.quality = "candidate"
        experience.success_count = 1
        experience.adopted_count = 0
        experience.revision = 1
        experience.indexed_revision = 0
        experience.last_used_at = datetime.now(UTC)
        self.executions.append(execution)
        self.experiences.append(experience)
        return experience

    async def record_failure(self, execution: QueryExecution) -> None:
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

    async def list_pending_index_deletions(
        self,
        user_id: int,
        role_name: str,
        *,
        limit: int,
    ) -> dict[UUID, int]:
        return dict(
            list(
                {
                    item.id: item.revision
                    for item in self.experiences
                    if item.owner_user_id == user_id
                    and item.role_name == role_name
                    and item.quality == "disabled"
                    and item.indexed_revision < item.revision
                }.items()
            )[:limit]
        )

    async def disable_by_resource_keys(
        self,
        resource_keys: set[str],
    ) -> dict[UUID, int]:
        experience_ids = {
            item.id
            for item in self.experiences
            if item.quality != "disabled"
            and any(asset.resource_key in resource_keys for asset in item.assets)
        }
        return await self.disable(experience_ids)

    async def disable(
        self,
        experience_ids: set[UUID] | list[UUID],
    ) -> dict[UUID, int]:
        revisions: dict[UUID, int] = {}
        for experience in self.experiences:
            if experience.id not in experience_ids or experience.quality == "disabled":
                continue
            experience.quality = "disabled"
            experience.revision += 1
            experience.invalidated_at = datetime.now(UTC)
            revisions[experience.id] = experience.revision
        return revisions

    async def promote_by_artifacts(
        self,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        session_id: str,
        artifact_paths: set[str],
    ) -> list[QueryExperience]:
        del user_id, conversation_id, analysis_id, session_id, artifact_paths
        return self.experiences

    async def get_many(
        self,
        user_id: int,
        experience_ids: list[UUID],
        *,
        role_name: str | None,
    ) -> list[QueryExperience]:
        by_id = {
            item.id: item
            for item in self.experiences
            if item.owner_user_id == user_id
            and (role_name is None or item.role_name == role_name)
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


def build_experience(
    *,
    table_name: str,
    column_name: str,
    meta_version: int,
    quality: str = "candidate",
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
        owner_user_id=7,
        role_name="analyst",
        fingerprint=uuid4().hex,
        dialect="doris",
        purposes=["统计订单金额"],
        representative_sql=f"SELECT {column_name} FROM {table_name}",
        sql_template=f"SELECT {column_name} FROM {table_name} WHERE id = :p1",
        quality=quality,
        success_count=4,
        adopted_count=2 if quality == "promoted" else 0,
        revision=2,
        indexed_revision=2,
        first_used_at=datetime.now(UTC),
        last_used_at=datetime.now(UTC),
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
            quality="disabled",
        )
        experience.invalidated_at = datetime.now(UTC)
        previous_revision = experience.revision
        previous_success_count = experience.success_count
        used_at = datetime.now(UTC)

        experience.refresh_from_success(
            purpose="按地区统计订单金额",
            representative_sql="SELECT region, SUM(amount) FROM orders GROUP BY region",
            sql_template="SELECT region, SUM(amount) FROM orders GROUP BY region",
            used_at=used_at,
        )

        self.assertEqual(experience.quality, "candidate")
        self.assertIsNone(experience.invalidated_at)
        self.assertEqual(experience.revision, previous_revision + 1)
        self.assertEqual(experience.success_count, previous_success_count + 1)
        self.assertEqual(experience.last_used_at, used_at)

        promoted = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            quality="promoted",
        )
        promoted.refresh_from_success(
            purpose="继续统计订单金额",
            representative_sql=promoted.representative_sql,
            sql_template=promoted.sql_template,
            used_at=used_at,
        )
        self.assertEqual(promoted.quality, "promoted")

    def test_sql_template_redacts_literals_and_has_stable_fingerprint(self) -> None:
        first_template, first_fingerprint = build_sql_template(
            "SELECT amount FROM orders WHERE region = '华东' AND day >= '2026-01-01'",
            "doris",
        )
        second_template, second_fingerprint = build_sql_template(
            "SELECT amount FROM orders WHERE region = '华南' AND day >= '2026-08-01'",
            "doris",
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
            dialect="doris",
            normalized_sql="SELECT amount FROM orders WHERE region = '华东' LIMIT 100",
            validation=QueryValidationResult(
                valid=True,
                dialect="doris",
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
                path="/analyses/sales/sessions/explorer/orders/query.csv",
                schema=[
                    QueryResultColumn(name="amount", type="integer", nullable=False)
                ],
                row_count=3,
                time_range={},
                sample=[{"amount": 42}],
            ),
        )

        experience_id = await service.record_success(
            QueryExecutionContext(
                user_id=7,
                role_name="analyst",
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

    async def test_search_disables_changed_assets_and_filters_latest_permissions(
        self,
    ) -> None:
        repo = FakePGRepo()
        index_repo = FakeIndexRepo()
        embedding_client = FakeEmbeddingClient()
        allowed = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            quality="promoted",
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
        foreign = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
        )
        foreign.owner_user_id = 8
        repo.experiences = [allowed, denied, stale, foreign]
        repo.current_versions = {asset.resource_key: 1 for asset in allowed.assets}
        repo.current_versions.update({asset.resource_key: 1 for asset in denied.assets})
        repo.current_versions.update(
            {
                asset.resource_key: 2 if asset.kind == "column" else 1
                for asset in stale.assets
            }
        )
        index_repo.text_hits = [
            SearchHit(item=foreign.id, score=20),
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

        results = await service.search(
            user_id=7,
            role_name="analyst",
            policy=policy,
            query="订单金额",
            table_names={"orders"},
            column_keys={("orders", "amount")},
            limit=5,
        )

        self.assertEqual([item.experience_id for item in results], [allowed.id])
        self.assertIn("final_artifact_adopted", results[0].match_reasons)
        self.assertIn("column_overlap", results[0].match_reasons)
        self.assertEqual(stale.quality, "disabled")
        self.assertIsNotNone(stale.invalidated_at)
        self.assertEqual(scheduler.enqueued, [(stale.id, stale.revision)])

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
                user_id=7,
                role_name="analyst",
                purpose="删除订单",
            ),
            session_key,
            raw_sql="DELETE FROM orders",
            dialect="doris",
            status="rejected",
            error_code="sql_validation_failed",
            error_detail="readonly query required",
        )

        self.assertEqual(len(repo.executions), 1)
        self.assertEqual(repo.executions[0].status, "rejected")
        self.assertIsNone(repo.executions[0].experience_id)
        self.assertEqual(repo.experiences, [])

    async def test_final_artifact_promotion_resynchronizes_semantic_index(self) -> None:
        repo = FakePGRepo()
        promoted = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            quality="promoted",
        )
        repo.experiences = [promoted]
        index_repo = FakeIndexRepo()
        scheduler = FakeIndexScheduler()
        service = build_service(
            repo,
            index_repo,
            FakeEmbeddingClient(),
            scheduler,
        )

        promoted_ids = await service.promote_by_artifacts(
            user_id=7,
            conversation_id=uuid4(),
            analysis_id="sales",
            session_id="orders",
            artifact_paths={"/analyses/sales/sessions/explorer/orders/query.csv"},
        )

        self.assertEqual(promoted_ids, [promoted.id])
        self.assertEqual(scheduler.enqueued, [(promoted.id, promoted.revision)])

    async def test_metadata_change_proactively_disables_matching_experiences(
        self,
    ) -> None:
        repo = FakePGRepo()
        invalid = build_experience(
            table_name="orders",
            column_name="amount",
            meta_version=1,
            quality="promoted",
        )
        retained = build_experience(
            table_name="customers",
            column_name="name",
            meta_version=1,
        )
        repo.experiences = [invalid, retained]
        index_repo = FakeIndexRepo()
        scheduler = FakeIndexScheduler()
        service = build_service(
            repo,
            index_repo,
            FakeEmbeddingClient(),
            scheduler,
        )

        invalidated_ids = await service.invalidate_assets(
            table_names={"orders"},
            column_keys=set(),
        )

        self.assertEqual(invalidated_ids, [invalid.id])
        self.assertEqual(invalid.quality, "disabled")
        self.assertEqual(retained.quality, "candidate")
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

        await service.invalidate_assets(
            table_names={"orders"},
            column_keys=set(),
        )
        self.assertLess(invalid.indexed_revision, invalid.revision)

        await service.sync_index(invalid.id, invalid.revision)

        self.assertEqual(index_repo.deleted, [invalid.id])
        self.assertEqual(invalid.indexed_revision, invalid.revision)


class QueryExperienceESRepoTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_many_removes_documents_from_index(self) -> None:
        experience_ids = [uuid4(), uuid4()]
        client = MagicMock()
        client.indices.exists = AsyncMock(return_value=True)
        client.delete_by_query = AsyncMock()
        repo = QueryExperienceESRepo(cast(Any, client))

        await repo.delete_many(experience_ids)

        client.delete_by_query.assert_awaited_once_with(
            index=repo._index_name,
            query={"ids": {"values": [str(item) for item in experience_ids]}},
            conflicts="proceed",
            refresh=True,
        )

    async def test_semantic_search_is_scoped_to_user_and_role(self) -> None:
        experience_id = uuid4()
        client = MagicMock()
        client.indices.exists = AsyncMock(return_value=True)
        client.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        {"_id": str(experience_id), "_score": 0.8},
                        {"_id": "invalid", "_score": 1},
                    ]
                }
            }
        )
        repo = QueryExperienceESRepo(cast(Any, client))

        hits = await repo.search_vector(
            [0.1, 0.2],
            user_id=7,
            role_name="analyst",
            limit=5,
        )

        self.assertEqual([hit.item for hit in hits], [experience_id])
        request = client.search.await_args.kwargs
        self.assertEqual(request["size"], 5)
        filters = request["knn"]["filter"]["bool"]["filter"]
        self.assertEqual(
            filters,
            [
                {"term": {"owner_user_id": 7}},
                {"term": {"role_name": "analyst"}},
            ],
        )
        self.assertEqual(
            request["knn"]["filter"]["bool"]["must_not"],
            [{"term": {"quality": "disabled"}}],
        )


if __name__ == "__main__":
    unittest.main()
