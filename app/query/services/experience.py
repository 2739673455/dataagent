"""查询执行记录、经验聚合与权限感知检索"""

import asyncio
import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from loguru import logger
from sqlglot import exp, parse_one

from app.identity.models.doris import asset_resource_key
from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.query.models.execution import QueryExecution, QueryExecutionStatus
from app.query.models.experience import (
    QueryAssetKind,
    QueryAssetSnapshot,
    QueryExperience,
    QueryExperienceAsset,
    QueryExperienceQuality,
    QueryExperienceSearchResult,
)
from app.query.models.validation import (
    QueryDialect,
    QueryValidationResult,
)
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.executor import SuccessfulQueryExecution
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.contracts.analysis import AgentSessionKey

_SEARCH_POOL_SIZE = 100
_RRF_K = 60
_INDEX_TEXT_MAX_CHARS = 8000


@dataclass(frozen=True, slots=True)
class QueryExecutionContext:
    """SQL 工具提供的用户、角色和任务上下文"""

    user_id: int
    role_name: str
    purpose: str
    tool_call_id: str | None = None


def build_sql_template(sql: str, dialect: QueryDialect) -> tuple[str, str]:
    """将 SQL 字面量替换为参数并生成稳定结构指纹"""
    expression = parse_one(sql, read=dialect)
    parameter_index = 0
    for node in list(expression.walk()):
        if not isinstance(node, exp.Literal):
            continue
        parameter_index += 1
        node.replace(exp.Placeholder(this=f"p{parameter_index}"))
    template = expression.sql(dialect=dialect, pretty=False)
    fingerprint = hashlib.sha256(f"{dialect}\0{template}".encode()).hexdigest()
    return template, fingerprint


class QueryExperienceService:
    """记录查询事实并检索经过当前权限校验的经验"""

    def __init__(
        self,
        repo: QueryExperiencePGRepo,
        index_repo: QueryExperienceESRepo,
        embedding_client: EmbeddingClient,
        index_scheduler: QueryExperienceIndexScheduler,
        *,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定查询经验存储、检索和索引调度依赖"""
        self._repo = repo
        self._index_repo = index_repo
        self._embedding_client = embedding_client
        self._index_scheduler = index_scheduler
        self._data_source = data_source
        self._database_name = database_name

    async def record_success(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> UUID:
        """记录成功执行并增量更新相同结构的查询经验"""
        sql_template, fingerprint = build_sql_template(
            details.normalized_sql,
            details.dialect,
        )
        tables = {item.name for item in details.validation.tables}
        columns = {(item.table, item.name) for item in details.validation.columns}
        async with self._repo.session.begin():
            table_versions, column_versions = await self._repo.metadata_versions(
                tables,
                columns,
            )
            experience_id = uuid4()
            experience = QueryExperience(
                id=experience_id,
                owner_user_id=context.user_id,
                role_name=context.role_name,
                fingerprint=fingerprint,
                dialect=details.dialect,
                purposes=[context.purpose],
                representative_sql=details.normalized_sql,
                sql_template=sql_template,
            )
            assets = self._build_assets(
                experience_id,
                details.validation,
                table_versions,
                column_versions,
            )
            execution = QueryExecution(
                user_id=context.user_id,
                role_name=context.role_name,
                conversation_id=details.session_key.conversation_id,
                analysis_id=details.session_key.analysis_id,
                session_id=details.session_key.session_id,
                tool_call_id=context.tool_call_id,
                purpose=context.purpose,
                raw_sql=details.raw_sql,
                normalized_sql=details.normalized_sql,
                sql_template=sql_template,
                fingerprint=fingerprint,
                dialect=details.dialect,
                status="succeeded",
                validation=details.validation.model_dump(mode="json"),
                plan_estimate=asdict(details.plan_estimate),
                result_summary={
                    "path": details.result.path,
                    "schema": [
                        item.model_dump(mode="json") for item in details.result.schema
                    ],
                    "row_count": details.result.row_count,
                    "time_range": {
                        key: value.model_dump(mode="json")
                        for key, value in details.result.time_range.items()
                    },
                },
                artifact_path=details.result.path,
            )
            stored = await self._repo.record_success(execution, experience, assets)
        self._index_scheduler.enqueue(stored.id, stored.revision)
        return stored.id

    async def record_failure(
        self,
        context: QueryExecutionContext,
        session_key: AgentSessionKey,
        *,
        raw_sql: str,
        dialect: QueryDialect,
        status: QueryExecutionStatus,
        error_code: str,
        error_detail: str,
        validation: QueryValidationResult | None = None,
    ) -> None:
        """记录被 Guard 拒绝或执行失败的 SQL"""
        execution = QueryExecution(
            user_id=context.user_id,
            role_name=context.role_name,
            conversation_id=session_key.conversation_id,
            analysis_id=session_key.analysis_id,
            session_id=session_key.session_id,
            tool_call_id=context.tool_call_id,
            purpose=context.purpose,
            raw_sql=raw_sql,
            dialect=dialect,
            status=status,
            error_code=error_code,
            error_detail=error_detail[:4000],
            validation=(
                validation.model_dump(mode="json") if validation is not None else None
            ),
        )
        async with self._repo.session.begin():
            await self._repo.record_failure(execution)

    async def promote_by_artifacts(
        self,
        *,
        user_id: int,
        conversation_id: UUID,
        analysis_id: str,
        session_id: str,
        artifact_paths: set[str],
    ) -> list[UUID]:
        """把最终 Explorer 结果直接采用的查询提升为正式经验"""
        async with self._repo.session.begin():
            experiences = await self._repo.promote_by_artifacts(
                user_id,
                conversation_id,
                analysis_id,
                session_id,
                artifact_paths,
            )
        for experience in experiences:
            self._index_scheduler.enqueue(experience.id, experience.revision)
        return [experience.id for experience in experiences]

    async def invalidate_assets(
        self,
        *,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
    ) -> list[UUID]:
        """禁用引用已变化元数据的经验并删除搜索索引"""
        resource_keys = {
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
            )
            for table_name in table_names
        }
        resource_keys.update(
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
                column_name,
            )
            for table_name, column_name in column_keys
        )
        async with self._repo.session.begin():
            revisions = await self._repo.disable_by_resource_keys(resource_keys)
        for experience_id, revision in revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        return list(revisions)

    async def search(
        self,
        *,
        user_id: int,
        role_name: str,
        policy: AssetAccessPolicy,
        query: str,
        table_names: set[str],
        column_keys: set[tuple[str, str]],
        limit: int,
    ) -> list[QueryExperienceSearchResult]:
        """融合语义、资产、质量和新鲜度检索查询经验"""
        semantic_ranks = await self._semantic_ranks(
            query,
            user_id=user_id,
            role_name=role_name,
        )
        resource_keys = {
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
            )
            for table_name in table_names
        }
        resource_keys.update(
            asset_resource_key(
                self._data_source,
                self._database_name,
                table_name,
                column_name,
            )
            for table_name, column_name in column_keys
        )
        async with self._repo.session.begin():
            semantic_ids = list(semantic_ranks)
            candidates = [
                *await self._repo.get_many(
                    user_id,
                    semantic_ids,
                    role_name=role_name,
                ),
                *await self._repo.find_by_assets(
                    user_id,
                    role_name,
                    resource_keys,
                    limit=_SEARCH_POOL_SIZE,
                ),
            ]
            if len(candidates) < limit:
                candidates.extend(
                    await self._repo.list_recent(
                        user_id,
                        role_name,
                        limit=_SEARCH_POOL_SIZE,
                    )
                )
            distinct = {item.id: item for item in candidates}
            experiences = list(distinct.values())
            current_versions = await self._repo.current_asset_versions(experiences)
            invalid_revisions = {
                experience.id: experience.revision
                for experience in experiences
                if experience.quality == "disabled"
            }
            stale_ids = {
                experience.id
                for experience in experiences
                if experience.quality != "disabled"
                and any(
                    current_versions.get(asset.resource_key) != asset.meta_version
                    for asset in experience.assets
                )
            }
            invalid_revisions.update(await self._repo.disable(stale_ids))
            experiences = [
                experience
                for experience in experiences
                if experience.id not in invalid_revisions
            ]
        for experience_id, revision in invalid_revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        authorization_filter = MetadataAuthorizationFilter(
            policy,
            self._data_source,
            self._database_name,
        )
        results = [
            result
            for experience in experiences
            if (
                result := self._score_experience(
                    experience,
                    semantic_ranks.get(experience.id, {}),
                    table_names,
                    column_keys,
                    authorization_filter,
                )
            )
            is not None
        ]
        return sorted(
            results,
            key=lambda item: (-item.score, -item.adopted_count, item.experience_id.hex),
        )[:limit]

    def _build_assets(
        self,
        experience_id: UUID,
        validation: QueryValidationResult,
        table_versions: dict[str, int],
        column_versions: dict[tuple[str, str], int],
    ) -> list[QueryExperienceAsset]:
        """按校验血缘构造带元数据版本的经验资产快照"""
        assets = [
            QueryExperienceAsset(
                experience_id=experience_id,
                kind="table",
                resource_key=asset_resource_key(
                    self._data_source,
                    table.database or self._database_name,
                    table.name,
                ),
                data_source=self._data_source,
                database_name=table.database or self._database_name,
                table_name=table.name,
                column_name=None,
                meta_version=table_versions.get(table.name, 0),
            )
            for table in validation.tables
        ]
        assets.extend(
            QueryExperienceAsset(
                experience_id=experience_id,
                kind="column",
                resource_key=asset_resource_key(
                    self._data_source,
                    column.database or self._database_name,
                    column.table,
                    column.name,
                ),
                data_source=self._data_source,
                database_name=column.database or self._database_name,
                table_name=column.table,
                column_name=column.name,
                meta_version=column_versions.get((column.table, column.name), 0),
            )
            for column in validation.columns
        )
        return assets

    async def sync_index(self, experience_id: UUID, requested_revision: int) -> int:
        """幂等同步一条查询经验的当前索引投影"""
        async with self._repo.session.begin():
            experience = await self._repo.get(experience_id)
        if experience is None:
            await self._index_repo.delete_many([experience_id])
            return requested_revision
        if experience.indexed_revision >= experience.revision:
            return experience.indexed_revision

        revision = experience.revision
        if experience.quality == "disabled":
            await self._index_repo.delete_many([experience.id])
        else:
            text = self._experience_text(experience)
            embeddings = await self._embedding_client.aembed_documents([text])
            if len(embeddings) != 1:
                raise ValueError("查询经验向量生成数量不匹配")
            await self._index_repo.index(
                experience.id,
                owner_user_id=experience.owner_user_id,
                role_name=experience.role_name,
                quality=experience.quality,
                text=text,
                embedding=embeddings[0],
            )

        async with self._repo.session.begin():
            await self._repo.mark_indexes_synced({experience.id: revision})
        return revision

    async def pending_index_repairs(self, *, limit: int) -> dict[UUID, int]:
        """读取待补偿的查询经验索引版本"""
        async with self._repo.session.begin():
            return await self._repo.list_pending_index_repairs(limit=limit)

    async def _semantic_ranks(
        self,
        query: str,
        *,
        user_id: int,
        role_name: str,
    ) -> dict[UUID, dict[str, float]]:
        """获取查询经验的文本和向量倒数排名分数"""
        try:
            embeddings = await self._embedding_client.aembed_documents([query])
            if len(embeddings) != 1:
                raise ValueError("查询经验检索向量生成数量不匹配")
            text_hits, vector_hits = await asyncio.gather(
                self._index_repo.search_text(
                    query,
                    user_id=user_id,
                    role_name=role_name,
                    limit=_SEARCH_POOL_SIZE,
                ),
                self._index_repo.search_vector(
                    embeddings[0],
                    user_id=user_id,
                    role_name=role_name,
                    limit=_SEARCH_POOL_SIZE,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("查询经验语义检索失败")
            return {}
        ranks: dict[UUID, dict[str, float]] = {}
        for channel, hits in (("text", text_hits), ("vector", vector_hits)):
            for rank, hit in enumerate(hits, start=1):
                ranks.setdefault(hit.item, {})[channel] = 1 / (_RRF_K + rank)
        return ranks

    def _score_experience(
        self,
        experience: QueryExperience,
        semantic_scores: dict[str, float],
        query_tables: set[str],
        query_columns: set[tuple[str, str]],
        authorization_filter: MetadataAuthorizationFilter,
    ) -> QueryExperienceSearchResult | None:
        """按资产权限、语义得分和血缘重叠度评估单条经验"""
        if experience.quality == "disabled":
            return None
        experience_tables = {
            asset.table_name for asset in experience.assets if asset.kind == "table"
        }
        experience_columns = {
            (asset.table_name, asset.column_name)
            for asset in experience.assets
            if asset.kind == "column" and asset.column_name is not None
        }
        if any(
            not authorization_filter.table_is_visible(table_name)
            for table_name in experience_tables
        ) or any(
            not authorization_filter.column_is_allowed(table_name, column_name)
            for table_name, column_name in experience_columns
        ):
            return None

        score = sum(semantic_scores.values()) * 12
        reasons = [f"{channel}_match" for channel in semantic_scores]
        if query_tables:
            coverage = len(query_tables & experience_tables) / len(query_tables)
            score += 0.2 * coverage
            if coverage:
                reasons.append("table_overlap")
        if query_columns:
            coverage = len(query_columns & experience_columns) / len(query_columns)
            score += 0.35 * coverage
            if coverage:
                reasons.append("column_overlap")
        if experience.quality == "promoted":
            score += 0.15
            reasons.append("final_artifact_adopted")
        score += min(0.05, math.log1p(experience.success_count) / 100)
        score += min(0.05, math.log1p(experience.adopted_count) / 50)
        last_used_at = experience.last_used_at
        if last_used_at.tzinfo is None:
            last_used_at = last_used_at.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - last_used_at).total_seconds() / 86400)
        score += 0.05 / (1 + age_days / 30)
        return QueryExperienceSearchResult(
            experience_id=experience.id,
            purpose=experience.purposes[-1],
            sql_template=experience.sql_template,
            dialect=experience.dialect,
            assets=[
                QueryAssetSnapshot(
                    kind=cast(QueryAssetKind, asset.kind),
                    database=asset.database_name,
                    table=asset.table_name,
                    column=asset.column_name,
                    meta_version=asset.meta_version,
                )
                for asset in sorted(
                    experience.assets,
                    key=lambda item: (
                        item.kind,
                        item.table_name,
                        item.column_name or "",
                    ),
                )
            ],
            quality=cast(QueryExperienceQuality, experience.quality),
            success_count=experience.success_count,
            adopted_count=experience.adopted_count,
            score=round(score, 6),
            match_reasons=list(dict.fromkeys(reasons)),
            last_used_at=experience.last_used_at,
        )

    @staticmethod
    def _experience_text(experience: QueryExperience) -> str:
        """构造不含历史字面量和结果样本的索引文本"""
        asset_names = [
            (
                f"{asset.table_name}.{asset.column_name}"
                if asset.column_name is not None
                else asset.table_name
            )
            for asset in experience.assets
        ]
        return "\n".join([*sorted(set(asset_names)), *experience.purposes[-5:]])[
            :_INDEX_TEXT_MAX_CHARS
        ]
