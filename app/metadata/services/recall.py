"""语义召回记录管理服务"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
)
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticRecallFailure,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticTableContext,
    SemanticValueRecallResult,
)
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.shared.contracts.query_experience import QueryExperienceRecallResult

_QUERY_EXPERIENCE_CACHE_TTL = timedelta(days=1)


class SemanticQueriesNotFoundError(Exception):
    """一个或多个查询业务键不存在"""

    def __init__(self, queries: list[str]) -> None:
        """初始化未找到的查询业务键"""
        self.queries = queries
        super().__init__(", ".join(queries))


def _stable_union[T](groups: list[list[T]]) -> list[T]:
    """稳定合并可哈希值列表"""
    result: list[T] = []
    seen: set[T] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
    return result


def _group_items[ItemT, KeyT](
    groups: list[list[ItemT]],
    key: Callable[[ItemT], KeyT],
) -> list[list[ItemT]]:
    """按资源主键稳定聚合多个召回结果"""
    grouped: dict[KeyT, list[ItemT]] = {}
    for items in groups:
        for item in items:
            grouped.setdefault(key(item), []).append(item)
    return list(grouped.values())


def _merge_metrics(
    responses: list[SemanticResourceRecallResponse],
) -> list[SemanticMetricRecallResult]:
    """按指标主键保留最新元数据快照"""
    return sorted(
        (
            max(
                matches,
                key=lambda item: (item.meta_version, item.rank_score),
            ).model_copy(deep=True)
            for matches in _group_items(
                [item.metrics for item in responses],
                lambda item: item.name,
            )
        ),
        key=lambda item: (-item.rank_score, item.name),
    )


def _column_score(item: SemanticColumnRecallResult) -> float:
    """将关系补充字段的空排名转换为可比较分数"""
    return item.rank_score if item.rank_score is not None else float("-inf")


def _merge_columns(
    responses: list[SemanticResourceRecallResponse],
) -> list[SemanticColumnRecallResult]:
    """按字段联合主键保留最新元数据快照"""
    result = [
        max(
            matches,
            key=lambda item: (item.meta_version, _column_score(item)),
        ).model_copy(deep=True)
        for matches in _group_items(
            [item.columns for item in responses],
            lambda item: (item.t_name, item.name),
        )
    ]
    return sorted(
        result,
        key=lambda item: (-_column_score(item), item.t_name, item.name),
    )


def _merge_values(
    responses: list[SemanticResourceRecallResponse],
) -> list[SemanticValueRecallResult]:
    """合并字段值结果并保留最高排名"""
    result: list[SemanticValueRecallResult] = []
    groups = _group_items(
        [item.values for item in responses],
        lambda x: (x.t_name, x.c_name, x.value),
    )
    for matches in groups:
        best = max(matches, key=lambda item: item.rank_score).model_copy(deep=True)
        best.match_reasons = _stable_union([item.match_reasons for item in matches])
        result.append(best)
    return sorted(
        result,
        key=lambda item: (-item.rank_score, item.t_name, item.c_name, item.value),
    )


def _merge_tables(
    responses: list[SemanticResourceRecallResponse],
) -> list[SemanticTableContext]:
    """按元数据版本合并表上下文"""
    groups = _group_items([item.tables for item in responses], lambda x: x.name)
    return sorted(
        (max(matches, key=lambda item: item.meta_version) for matches in groups),
        key=lambda item: item.name,
    )


def merge_semantic_recall_responses(
    recall_id: str,
    responses: list[SemanticResourceRecallResponse],
    *,
    refresh_request: SemanticResourceRecallRequest | None = None,
) -> SemanticResourceRecallResponse:
    """生成多个召回结果的去重合并快照"""
    if len(responses) < 2:
        raise ValueError("至少需要两个召回响应")
    failures = _merge_failures(responses, refresh_request)
    return SemanticResourceRecallResponse(
        status="partial" if failures else "success",
        recall_id=recall_id,
        terms=_stable_union([response.terms for response in responses]),
        metrics=_merge_metrics(responses),
        columns=_merge_columns(responses),
        values=_merge_values(responses),
        tables=_merge_tables(responses),
        failures=failures,
        warnings=_stable_union([response.warnings for response in responses]),
        truncated=any(response.truncated for response in responses),
    )


def _merge_failures(
    responses: list[SemanticResourceRecallResponse],
    refresh_request: SemanticResourceRecallRequest | None,
) -> list[SemanticRecallFailure]:
    """合并失败范围，并在本次成功覆盖时清除旧失败"""
    failures = _stable_union([response.failures for response in responses])
    if refresh_request is None:
        return failures

    latest_failures = set(responses[-1].failures)
    return [
        failure
        for failure in failures
        if failure in latest_failures
        or not _failure_is_refreshed(failure, refresh_request)
    ]


def _failure_is_refreshed(
    failure: SemanticRecallFailure,
    request: SemanticResourceRecallRequest,
) -> bool:
    """判断本次请求是否覆盖了一个旧失败范围"""
    if failure.resource_type not in request.resource_types:
        return False
    if failure.resource_type == "value" and failure.channel != "fulltext":
        return False
    return failure.term is None or failure.term in request.terms


def _remove_semantic_resources(
    response: SemanticResourceRecallResponse,
    deletion: SemanticRecallResourceDeletion,
) -> SemanticResourceRecallResponse:
    """移除资源并保持字段、指标、表上下文之间的一致性"""
    removed_tables = {
        table_name
        for table_name, table_deletion in deletion.tables.items()
        if table_deletion.deletes_entire_table
    }
    removed_columns = {
        (table_name, column_name)
        for table_name, table_deletion in deletion.tables.items()
        if table_deletion.columns is not None
        for column_name, column_deletion in table_deletion.columns.items()
        if column_deletion.deletes_entire_column
    }
    removed_values = {
        (table_name, column_name, value)
        for table_name, table_deletion in deletion.tables.items()
        if table_deletion.columns is not None
        for column_name, column_deletion in table_deletion.columns.items()
        if column_deletion.values is not None
        for value in column_deletion.values
    }
    removed_metrics = set(deletion.metrics)

    remaining_columns = [
        item
        for item in response.columns
        if item.t_name not in removed_tables
        and (item.t_name, item.name) not in removed_columns
    ]
    remaining_column_keys = {
        (item.t_name, item.name) for item in remaining_columns
    }
    remaining_values = [
        item
        for item in response.values
        if (item.t_name, item.c_name) in remaining_column_keys
        and (item.t_name, item.c_name, item.value) not in removed_values
    ]
    remaining_metrics = [
        item
        for item in response.metrics
        if item.name not in removed_metrics
        and all(
            (reference["t_name"], reference["c_name"]) in remaining_column_keys
            for reference in item.relevant_columns
        )
    ]
    table_names = {item.t_name for item in remaining_columns}
    remaining_tables = [
        item.model_copy(
            update={
                "primary_key_columns": [
                    column_name
                    for column_name in item.primary_key_columns
                    if (item.name, column_name) in remaining_column_keys
                ]
            }
        )
        for item in response.tables
        if item.name in table_names
    ]
    remaining_columns = [
        item.model_copy(
            update={
                "reference_t_name": (
                    item.reference_t_name
                    if (item.reference_t_name, item.reference_c_name)
                    in remaining_column_keys
                    else None
                ),
                "reference_c_name": (
                    item.reference_c_name
                    if (item.reference_t_name, item.reference_c_name)
                    in remaining_column_keys
                    else None
                ),
            }
        )
        for item in remaining_columns
    ]
    return response.model_copy(
        update={
            "metrics": remaining_metrics,
            "columns": remaining_columns,
            "values": remaining_values,
            "tables": remaining_tables,
        }
    )


class SemanticRecallContextService:
    """记录、查询、合并和删除会话级语义召回"""

    def __init__(
        self,
        repo: SemanticRecallPGRepo,
        authorization_filter: MetadataAuthorizationFilter,
    ) -> None:
        """初始化召回管理服务"""
        self._repo = repo
        self._authorization_filter = authorization_filter

    async def record(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
        request: SemanticResourceRecallRequest,
        response: SemanticResourceRecallResponse,
        query_experiences: list[QueryExperienceRecallResult],
        query_experiences_retrieved_at: datetime,
    ) -> SemanticRecallRecord:
        """将一次检索结果增量合入 query 的持续上下文"""
        now = datetime.now(UTC)
        await self._repo.acquire_query_lock(user_id, conversation_id, query)
        previous = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            query,
        )
        if previous is not None:
            previous = self._authorize_record(previous)
            response = merge_semantic_recall_responses(
                response.recall_id,
                [previous.response, response],
                refresh_request=request,
            )
        record = SemanticRecallRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            query=query,
            request=request,
            response=self._authorization_filter.filter_recall_response(response),
            query_experiences=self._filter_query_experiences(query_experiences),
            query_experiences_retrieved_at=query_experiences_retrieved_at,
            source_queries=(previous.source_queries if previous is not None else []),
            created_at=now,
        )
        await self._repo.save(record)
        return record

    async def get_fresh_query_experiences(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
        *,
        now: datetime | None = None,
    ) -> tuple[list[QueryExperienceRecallResult], datetime] | None:
        """读取当前查询在一天有效期内的查询经验结果"""
        record = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            query,
        )
        if record is None:
            return None
        retrieved_at = record.query_experiences_retrieved_at
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        current_time = now or datetime.now(UTC)
        if current_time - retrieved_at >= _QUERY_EXPERIENCE_CACHE_TTL:
            return None
        authorized = self._authorize_record(record)
        return authorized.query_experiences, retrieved_at

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
    ) -> SemanticRecallRecord:
        """按 query 获取最新召回记录，不存在时给出明确错误"""
        record = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            query,
        )
        if record is None:
            raise SemanticQueriesNotFoundError([query])
        return self._authorize_record(record)

    async def list(
        self,
        user_id: int,
        conversation_id: UUID,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[SemanticRecallRecord]:
        """列出会话召回记录"""
        if limit <= 0:
            raise ValueError("limit 必须为正整数")
        if offset < 0:
            raise ValueError("offset 不能为负数")
        return [
            self._authorize_record(record)
            for record in await self._repo.list(
                user_id,
                conversation_id,
                limit=limit,
                offset=offset,
            )
        ]

    async def merge(
        self,
        user_id: int,
        conversation_id: UUID,
        target_query: str,
        source_query: str,
    ) -> SemanticRecallRecord:
        """将来源 query 的语义资源吸收到目标并删除来源"""
        if target_query == source_query:
            raise ValueError("目标 query 和来源 query 不能相同")

        for query in sorted((target_query, source_query)):
            await self._repo.acquire_query_lock(user_id, conversation_id, query)

        target_record = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            target_query,
        )
        source_record = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            source_query,
        )
        if target_record is None or source_record is None:
            missing = [
                query
                for query, record in (
                    (target_query, target_record),
                    (source_query, source_record),
                )
                if record is None
            ]
            raise SemanticQueriesNotFoundError(missing)
        target_record = self._authorize_record(target_record)
        source_record = self._authorize_record(source_record)

        now = datetime.now(UTC)
        merged_id = f"recall_{uuid.uuid4().hex}"
        absorbed_queries = _stable_union(
            [
                target_record.source_queries,
                [source_query],
                source_record.source_queries,
            ]
        )
        absorbed_queries = [
            query for query in absorbed_queries if query != target_query
        ]
        merged = SemanticRecallRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            query=target_query,
            request=None,
            response=merge_semantic_recall_responses(
                merged_id,
                [target_record.response, source_record.response],
            ),
            query_experiences=target_record.query_experiences,
            query_experiences_retrieved_at=(
                target_record.query_experiences_retrieved_at
            ),
            source_queries=absorbed_queries,
            created_at=now,
        )
        await self._repo.save(merged)
        await self._repo.delete_by_query(user_id, conversation_id, source_query)
        return merged

    def _authorize_record(
        self,
        record: SemanticRecallRecord,
    ) -> SemanticRecallRecord:
        """按当前策略生成召回记录的安全读取副本"""
        response = self._authorization_filter.filter_recall_response(record.response)
        query_experiences = self._filter_query_experiences(record.query_experiences)
        return record.model_copy(
            update={
                "response": response,
                "query_experiences": query_experiences,
            }
        )

    def _filter_query_experiences(
        self,
        experiences: list[QueryExperienceRecallResult],
    ) -> list[QueryExperienceRecallResult]:
        """移除包含当前用户不可见资产的查询经验"""
        return [
            experience
            for experience in experiences
            if all(
                self._authorization_filter.table_is_visible(asset.table)
                if asset.kind == "table"
                else asset.column is not None
                and self._authorization_filter.column_is_allowed(
                    asset.table,
                    asset.column,
                )
                for asset in experience.assets
            )
        ]

    async def delete(
        self,
        user_id: int,
        conversation_id: UUID,
        deletions: list[SemanticRecallResourceDeletion],
    ) -> list[SemanticRecallRecord]:
        """按 query 删除资源并返回各 query 的最终上下文"""
        loaded: list[tuple[SemanticRecallResourceDeletion, SemanticRecallRecord]] = []
        missing: list[str] = []
        for query in sorted(deletion.query for deletion in deletions):
            await self._repo.acquire_query_lock(user_id, conversation_id, query)
        for deletion in deletions:
            record = await self._repo.get_latest_by_query(
                user_id,
                conversation_id,
                deletion.query,
            )
            if record is None:
                missing.append(deletion.query)
                continue
            loaded.append((deletion, self._authorize_record(record)))
        if missing:
            raise SemanticQueriesNotFoundError(missing)

        results: list[SemanticRecallRecord] = []
        for deletion, record in loaded:
            if deletion.deletes_entire_query:
                await self._repo.delete_by_query(
                    user_id,
                    conversation_id,
                    deletion.query,
                )
                results.append(
                    record.model_copy(
                        update={
                            "request": None,
                            "response": record.response.model_copy(
                                update={
                                    "recall_id": f"recall_{uuid.uuid4().hex}",
                                    "metrics": [],
                                    "columns": [],
                                    "values": [],
                                    "tables": [],
                                }
                            ),
                            "query_experiences": [],
                            "created_at": datetime.now(UTC),
                        }
                    )
                )
                continue

            response = _remove_semantic_resources(record.response, deletion)
            removed_experience_ids = {
                item.id for item in deletion.query_experiences
            }
            query_experiences = [
                experience
                for experience in record.query_experiences
                if experience.id not in removed_experience_ids
            ]
            if (
                response == record.response
                and query_experiences == record.query_experiences
            ):
                results.append(record)
                continue
            updated_record = record.model_copy(
                update={
                    "request": None,
                    "response": response.model_copy(
                        update={"recall_id": f"recall_{uuid.uuid4().hex}"}
                    ),
                    "query_experiences": query_experiences,
                    "created_at": datetime.now(UTC),
                }
            )
            await self._repo.save(updated_record)
            results.append(updated_record)
        return results
