"""语义召回记录管理服务"""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.metadata.recall_models import SemanticRecallRecord
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.search_models import (
    SemanticColumnResult,
    SemanticMetricResult,
    SemanticRelation,
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticTableContext,
    SemanticValueResult,
)
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter


class SemanticRecallsNotFoundError(Exception):
    """一个或多个召回记录不存在"""

    def __init__(self, recall_ids: list[str]) -> None:
        self.recall_ids = recall_ids
        super().__init__(", ".join(recall_ids))


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
        raise ValueError("at least two recall responses are required")
    return SemanticSearchResponse(
        status=(
            "partial"
            if any(response.status == "partial" for response in responses)
            else "success"
        ),
        search_id=recall_id,
        queries=_stable_union([response.queries for response in responses]),
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
        request: SemanticSearchRequest,
        response: SemanticSearchResponse,
    ) -> SemanticRecallRecord:
        """持久化一次原始查询及完整召回结果"""
        now = datetime.now(UTC)
        record = SemanticRecallRecord(
            recall_id=response.search_id,
            user_id=user_id,
            conversation_id=conversation_id,
            kind="search",
            request=request,
            response=self._authorization_filter.filter_semantic_response(response),
            source_recall_ids=[],
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(record)
        return record

    async def get(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_id: str,
    ) -> SemanticRecallRecord:
        """获取召回记录，不存在时给出明确错误"""
        record = await self._repo.get(user_id, conversation_id, recall_id)
        if record is None:
            raise SemanticRecallsNotFoundError([recall_id])
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
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset cannot be negative")
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
        recall_ids: list[str],
    ) -> SemanticRecallRecord:
        """创建保留源记录的去重合并快照"""
        source_ids = list(dict.fromkeys(recall_ids))
        if len(source_ids) < 2:
            raise ValueError("at least two distinct recall IDs are required")

        records: list[SemanticRecallRecord] = []
        missing: list[str] = []
        for recall_id in source_ids:
            record = await self._repo.get(user_id, conversation_id, recall_id)
            if record is None:
                missing.append(recall_id)
            else:
                records.append(self._authorize_record(record))
        if missing:
            raise SemanticRecallsNotFoundError(missing)

        now = datetime.now(UTC)
        merged_id = f"recall_{uuid.uuid4().hex}"
        merged = SemanticRecallRecord(
            recall_id=merged_id,
            user_id=user_id,
            conversation_id=conversation_id,
            kind="merged",
            request=None,
            response=merge_semantic_search_responses(
                merged_id,
                [record.response for record in records],
            ),
            source_recall_ids=source_ids,
            created_at=now,
            updated_at=now,
        )
        await self._repo.save(merged)
        return merged

    def _authorize_record(
        self,
        record: SemanticRecallRecord,
    ) -> SemanticRecallRecord:
        """按当前策略生成召回记录的安全读取副本"""
        response = self._authorization_filter.filter_semantic_response(
            record.response
        )
        if response is record.response:
            return record
        return record.model_copy(update={"response": response})

    async def delete(
        self,
        user_id: int,
        conversation_id: UUID,
        recall_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        """批量删除并分别返回已删除和不存在的记录 ID"""
        deleted: list[str] = []
        missing: list[str] = []
        for recall_id in dict.fromkeys(recall_ids):
            if await self._repo.delete(user_id, conversation_id, recall_id):
                deleted.append(recall_id)
            else:
                missing.append(recall_id)
        return deleted, missing
