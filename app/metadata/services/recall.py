"""语义召回记录管理服务"""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.metadata.models.recall import SemanticRecallRecord
from app.metadata.models.search import (
    SemanticColumnResult,
    SemanticMetricResult,
    SemanticRelation,
    SemanticResourceSearchRequest,
    SemanticSearchResponse,
    SemanticTableContext,
    SemanticValueResult,
)
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.shared.contracts.query_experience import QueryExperienceSearchResult

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


def _stable_json_union(groups: list[list[Any]]) -> list[Any]:
    """稳定合并任意 JSON 值列表"""
    result: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
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
    responses: list[SemanticSearchResponse],
) -> list[SemanticMetricResult]:
    """合并指标结果并保留最高排名快照和全部命中依据"""
    result: list[SemanticMetricResult] = []
    for matches in _group_items([item.metrics for item in responses], lambda x: x.name):
        best = max(matches, key=lambda item: item.rank_score).model_copy(deep=True)
        best.alias = _stable_union([item.alias for item in matches])
        best.relevant_columns = _stable_json_union(
            [item.relevant_columns for item in matches]
        )
        best.match_reasons = _stable_union([item.match_reasons for item in matches])
        result.append(best)
    return sorted(result, key=lambda item: (-item.rank_score, item.name))


def _column_score(item: SemanticColumnResult) -> float:
    """将关系补充字段的空排名转换为可比较分数"""
    return item.rank_score if item.rank_score is not None else float("-inf")


def _merge_columns(
    responses: list[SemanticSearchResponse],
) -> list[SemanticColumnResult]:
    """合并字段结果并累计引入原因"""
    result: list[SemanticColumnResult] = []
    groups = _group_items(
        [item.columns for item in responses],
        lambda x: (x.t_name, x.name),
    )
    for matches in groups:
        best = max(matches, key=_column_score).model_copy(deep=True)
        best.alias = _stable_union([item.alias for item in matches])
        best.examples = _stable_json_union([item.examples for item in matches])
        best.inclusion_reasons = _stable_union(
            [item.inclusion_reasons for item in matches]
        )
        best.match_reasons = _stable_union([item.match_reasons for item in matches])
        result.append(best)
    return sorted(
        result,
        key=lambda item: (-_column_score(item), item.t_name, item.name),
    )


def _merge_values(
    responses: list[SemanticSearchResponse],
) -> list[SemanticValueResult]:
    """合并字段值结果并保留最高排名"""
    result: list[SemanticValueResult] = []
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
    responses: list[SemanticSearchResponse],
) -> list[SemanticTableContext]:
    """按元数据版本合并表上下文"""
    groups = _group_items([item.tables for item in responses], lambda x: x.name)
    return sorted(
        (max(matches, key=lambda item: item.meta_version) for matches in groups),
        key=lambda item: item.name,
    )


def _merge_relations(
    responses: list[SemanticSearchResponse],
) -> list[SemanticRelation]:
    """稳定去重字段关系"""
    groups = _group_items(
        [item.relations for item in responses],
        lambda x: (
            x.source_t_name,
            x.source_c_name,
            x.target_t_name,
            x.target_c_name,
            x.type,
        ),
    )
    return [matches[0] for matches in groups]


def merge_semantic_search_responses(
    recall_id: str,
    responses: list[SemanticSearchResponse],
) -> SemanticSearchResponse:
    """生成多个召回结果的去重合并快照"""
    if len(responses) < 2:
        raise ValueError("至少需要两个召回响应")
    return SemanticSearchResponse(
        status=(
            "partial"
            if any(response.status == "partial" for response in responses)
            else "success"
        ),
        search_id=recall_id,
        terms=_stable_union([response.terms for response in responses]),
        metrics=_merge_metrics(responses),
        columns=_merge_columns(responses),
        values=_merge_values(responses),
        tables=_merge_tables(responses),
        relations=_merge_relations(responses),
        warnings=_stable_union([response.warnings for response in responses]),
        truncated=any(response.truncated for response in responses),
    )


class SemanticRecallService:
    """记录、查询、合并和删除会话级语义召回"""

    def __init__(
        self,
        repo: SemanticRecallPGRepo,
        authorization_filter: MetadataAuthorizationFilter,
    ) -> None:
        """初始化召回管理服务"""
        self._repo = repo
        self._authorization_filter = authorization_filter

    async def record_search(
        self,
        user_id: int,
        conversation_id: UUID,
        query: str,
        request: SemanticResourceSearchRequest,
        response: SemanticSearchResponse,
        query_experiences: list[QueryExperienceSearchResult],
        query_experiences_retrieved_at: datetime,
    ) -> SemanticRecallRecord:
        """将一次检索结果增量合入 query 的持续上下文"""
        now = datetime.now(UTC)
        previous = await self._repo.get_latest_by_query(
            user_id,
            conversation_id,
            query,
        )
        if previous is not None:
            previous = self._authorize_record(previous)
            response = merge_semantic_search_responses(
                response.search_id,
                [previous.response, response],
            )
        record = SemanticRecallRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            query=query,
            request=request,
            response=self._authorization_filter.filter_semantic_response(response),
            query_experiences=self._filter_query_experiences(query_experiences),
            query_experiences_retrieved_at=query_experiences_retrieved_at,
            source_queries=(previous.source_queries if previous is not None else []),
            created_at=now,
            updated_at=now,
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
    ) -> tuple[list[QueryExperienceSearchResult], datetime] | None:
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
        missing = [
            query
            for query, record in (
                (target_query, target_record),
                (source_query, source_record),
            )
            if record is None
        ]
        if missing:
            raise SemanticQueriesNotFoundError(missing)
        assert target_record is not None
        assert source_record is not None
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
            response=merge_semantic_search_responses(
                merged_id,
                [target_record.response, source_record.response],
            ),
            query_experiences=target_record.query_experiences,
            query_experiences_retrieved_at=(
                target_record.query_experiences_retrieved_at
            ),
            source_queries=absorbed_queries,
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(merged)
        await self._repo.delete_by_query(user_id, conversation_id, source_query)
        return merged

    def _authorize_record(
        self,
        record: SemanticRecallRecord,
    ) -> SemanticRecallRecord:
        """按当前策略生成召回记录的安全读取副本"""
        response = self._authorization_filter.filter_semantic_response(record.response)
        query_experiences = self._filter_query_experiences(record.query_experiences)
        if (
            response is record.response
            and query_experiences == record.query_experiences
        ):
            return record
        return record.model_copy(
            update={
                "response": response,
                "query_experiences": query_experiences,
            }
        )

    def _filter_query_experiences(
        self,
        experiences: list[QueryExperienceSearchResult],
    ) -> list[QueryExperienceSearchResult]:
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
        queries: list[str],
    ) -> tuple[list[str], list[str]]:
        """批量删除 query 的全部快照并返回处理结果"""
        deleted: list[str] = []
        missing: list[str] = []
        for query in dict.fromkeys(queries):
            if await self._repo.delete_by_query(user_id, conversation_id, query):
                deleted.append(query)
            else:
                missing.append(query)
        return deleted, missing
