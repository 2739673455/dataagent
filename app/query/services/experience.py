"""查询执行记录、经验聚合与权限感知检索"""

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from typing import Protocol, cast
from uuid import UUID, uuid4

from loguru import logger
from sqlglot import exp, parse_one

from app.identity.models.doris import asset_resource_key
from app.identity.services.authorization import AssetAccessPolicy
from app.metadata.models.search import SearchHit
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.query.models.execution import QueryExecution, QueryExecutionStatus
from app.query.models.experience import (
    QueryExperience,
    QueryExperienceAsset,
)
from app.query.models.validation import QueryValidationResult
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.executor import SuccessfulQueryExecution
from app.shared.clients.embedding_client_manager import EmbeddingClient
from app.shared.config.app_config import cfg
from app.shared.contracts.analysis import AgentSessionKey
from app.shared.contracts.query_experience import (
    QueryAssetKind,
    QueryAssetSnapshot,
    QueryExperienceRecall,
    QueryExperienceRecallResult,
    QueryExperienceRecallStatus,
)

_SEARCH_POOL_SIZE = 100
_RRF_K = 60
_INDEX_TEXT_MAX_CHARS = 8000


class QueryExperienceIndexScheduler(Protocol):
    """查询经验索引任务调度能力"""

    def enqueue(self, experience_id: UUID, revision: int) -> None:
        """提交指定经验版本的索引同步任务"""
        ...


@dataclass(frozen=True, slots=True)
class QueryExecutionContext:
    """SQL 工具提供的用户、角色和任务上下文"""

    session_key: AgentSessionKey
    role_name: str
    authorization_epoch: UUID
    purpose: str
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class _QueryExperienceSemanticRecall:
    """查询经验索引通道的内部融合结果"""

    status: QueryExperienceRecallStatus
    ranks: dict[UUID, float]


def _build_sql_template(sql: str) -> tuple[str, str]:
    """将 SQL 字面量替换为参数并生成稳定结构指纹"""
    expression = parse_one(sql, read="doris")
    parameter_index = 0
    for node in list(expression.walk()):
        if not isinstance(node, exp.Literal):
            continue
        parameter_index += 1
        node.replace(exp.Placeholder(this=f"p{parameter_index}"))
    template = expression.sql(dialect="doris", pretty=False)
    fingerprint = hashlib.sha256(template.encode()).hexdigest()
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
    ) -> UUID | None:
        """记录成功执行并增量更新相同结构的查询经验"""
        if details.validation.query_kind == "catalog":
            await self._record_catalog_success(context, details)
            return None
        if details.plan_estimate is None:
            raise ValueError("业务查询成功记录缺少执行计划估算")
        sql_template, fingerprint = _build_sql_template(details.normalized_sql)
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
                role_name=context.role_name,
                authorization_epoch=context.authorization_epoch,
                fingerprint=fingerprint,
                purposes=[context.purpose],
                sql_template=sql_template,
            )
            assets = self._build_assets(
                experience_id,
                details.validation,
                table_versions,
                column_versions,
            )
            execution = QueryExecution(
                user_id=context.session_key.user_id,
                role_name=context.role_name,
                authorization_epoch=context.authorization_epoch,
                conversation_id=context.session_key.conversation_id,
                analysis_id=context.session_key.analysis_id,
                session_id=context.session_key.session_id,
                tool_call_id=context.tool_call_id,
                purpose=context.purpose,
                raw_sql=details.raw_sql,
                normalized_sql=details.normalized_sql,
                sql_template=sql_template,
                fingerprint=fingerprint,
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
            )
            stored = await self._repo.record_success(execution, experience, assets)
        self._index_scheduler.enqueue(stored.id, stored.revision)
        return stored.id

    async def _record_catalog_success(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> None:
        """仅审计成功目录查询，不生成可召回的业务查询经验"""
        execution = QueryExecution(
            user_id=context.session_key.user_id,
            role_name=context.role_name,
            authorization_epoch=context.authorization_epoch,
            conversation_id=context.session_key.conversation_id,
            analysis_id=context.session_key.analysis_id,
            session_id=context.session_key.session_id,
            tool_call_id=context.tool_call_id,
            purpose=context.purpose,
            raw_sql=details.raw_sql,
            normalized_sql=details.normalized_sql,
            status="succeeded",
            validation=details.validation.model_dump(mode="json"),
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
        )
        async with self._repo.session.begin():
            await self._repo.record_execution(execution)

    async def record_failure(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        status: QueryExecutionStatus,
        error_code: str,
        error_detail: str,
        validation: QueryValidationResult | None = None,
    ) -> None:
        """记录被 Guard 拒绝或执行失败的 SQL"""
        execution = QueryExecution(
            user_id=context.session_key.user_id,
            role_name=context.role_name,
            authorization_epoch=context.authorization_epoch,
            conversation_id=context.session_key.conversation_id,
            analysis_id=context.session_key.analysis_id,
            session_id=context.session_key.session_id,
            tool_call_id=context.tool_call_id,
            purpose=context.purpose,
            raw_sql=raw_sql,
            status=status,
            error_code=error_code,
            error_detail=error_detail[:4000],
            validation=(
                validation.model_dump(mode="json") if validation is not None else None
            ),
        )
        async with self._repo.session.begin():
            await self._repo.record_failure(execution)

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
            revisions = await self._repo.disable_for_changed_resources(resource_keys)
        for experience_id, revision in revisions.items():
            self._index_scheduler.enqueue(experience_id, revision)
        return list(revisions)

    async def recall(
        self,
        *,
        role_name: str,
        authorization_epoch: UUID,
        policy: AssetAccessPolicy,
        query: str,
        limit: int,
    ) -> QueryExperienceRecall:
        """按混合语义排名检索查询经验"""
        semantic_recall = await self._semantic_recall(
            query,
            role_name=role_name,
            authorization_epoch=authorization_epoch,
        )
        if semantic_recall.status == "failed":
            return QueryExperienceRecall(status="failed", results=[])
        semantic_ranks = semantic_recall.ranks
        async with self._repo.session.begin():
            semantic_ids = list(semantic_ranks)
            experiences = await self._repo.get_many(
                semantic_ids,
                role_name=role_name,
                authorization_epoch=authorization_epoch,
            )
            current_versions = await self._repo.current_asset_versions(experiences)
            invalid_revisions = {
                experience.id: experience.revision
                for experience in experiences
                if experience.status != "active"
            }
            stale_ids = {
                experience.id
                for experience in experiences
                if experience.status == "active"
                and any(
                    current_versions.get(asset.resource_key) != asset.meta_version
                    for asset in experience.assets
                )
            }
            invalid_revisions.update(
                await self._repo.disable_for_metadata_change(stale_ids)
            )
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
        ordered_experiences = sorted(
            experiences,
            key=lambda item: (-semantic_ranks[item.id], item.id.hex),
        )
        results = [
            result
            for experience in ordered_experiences
            if (
                result := self._to_recall_result(
                    experience,
                    authorization_filter,
                )
            )
            is not None
        ][:limit]
        return QueryExperienceRecall(
            status=semantic_recall.status,
            results=results,
        )

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
            await self._index_repo.delete(
                experience_id,
                revision=requested_revision,
            )
            return requested_revision
        if experience.indexed_revision >= experience.revision:
            return experience.indexed_revision

        revision = experience.revision
        if experience.status == "deleting":
            await self._index_repo.delete(
                experience.id,
                revision=revision,
            )
            async with self._repo.session.begin():
                await self._repo.finalize_deletion(experience.id, revision)
            return revision
        if experience.status == "disabled":
            await self._index_repo.delete(
                experience.id,
                revision=revision,
            )
        else:
            text = self._experience_text(experience)
            embeddings = await self._embedding_client.aembed_documents([text])
            if len(embeddings) != 1:
                raise ValueError("查询经验向量生成数量不匹配")
            await self._index_repo.index(
                experience.id,
                revision=revision,
                role_name=experience.role_name,
                authorization_epoch=experience.authorization_epoch,
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

    async def _semantic_recall(
        self,
        query: str,
        *,
        role_name: str,
        authorization_epoch: UUID,
    ) -> _QueryExperienceSemanticRecall:
        """分别召回全文和向量候选，并融合可用通道"""
        text_task = asyncio.create_task(
            self._index_repo.search_text(
                query,
                role_name=role_name,
                authorization_epoch=authorization_epoch,
                limit=_SEARCH_POOL_SIZE,
            )
        )
        vector_task: asyncio.Task[list[SearchHit[UUID]]] | None = None
        try:
            embeddings = await self._embedding_client.aembed_documents([query])
            if len(embeddings) != 1:
                raise ValueError("查询经验检索向量生成数量不匹配")
            vector_task = asyncio.create_task(
                self._index_repo.search_vector(
                    embeddings[0],
                    role_name=role_name,
                    authorization_epoch=authorization_epoch,
                    limit=_SEARCH_POOL_SIZE,
                    min_score=cfg.query.query_experience_vector_score_threshold,
                )
            )
        except asyncio.CancelledError:
            text_task.cancel()
            await asyncio.gather(text_task, return_exceptions=True)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("查询经验向量生成失败")

        text_hits = await self._await_hits(text_task, "全文")
        vector_hits = (
            await self._await_hits(vector_task, "向量")
            if vector_task is not None
            else None
        )
        available_hits = [hits for hits in (text_hits, vector_hits) if hits is not None]
        if not available_hits:
            return _QueryExperienceSemanticRecall(status="failed", ranks={})
        ranks: dict[UUID, float] = {}
        for hits in available_hits:
            for rank, hit in enumerate(hits, start=1):
                ranks[hit.item] = ranks.get(hit.item, 0) + 1 / (_RRF_K + rank)
        return _QueryExperienceSemanticRecall(
            status="success" if len(available_hits) == 2 else "partial",
            ranks=ranks,
        )

    @staticmethod
    async def _await_hits(
        task: asyncio.Task[list[SearchHit[UUID]]],
        channel: str,
    ) -> list[SearchHit[UUID]] | None:
        """等待单个检索通道，保留另一路的结果"""
        try:
            return await task
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(f"查询经验{channel}检索失败")
            return None

    def _to_recall_result(
        self,
        experience: QueryExperience,
        authorization_filter: MetadataAuthorizationFilter,
    ) -> QueryExperienceRecallResult | None:
        """将已通过有效性检查的经验转换为模型可用结果"""
        assets = [
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
        ]
        if not authorization_filter.query_experience_is_allowed(assets):
            return None
        return QueryExperienceRecallResult(
            id=experience.id,
            purpose=experience.purposes[-1],
            sql_template=experience.sql_template,
            assets=assets,
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
