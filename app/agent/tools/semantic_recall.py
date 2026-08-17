"""语义资源召回与记录管理工具"""

from typing import Annotated, Any, Literal
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from pydantic import ValidationError

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.entities.semantic_search import SemanticSearchRequest
from app.repositories.column_es_repo import ColumnESRepo
from app.repositories.meta_pg_repo import MetaPGRepo
from app.repositories.metric_es_repo import MetricESRepo
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.repositories.value_es_repo import ValueESRepo
from app.services.meta_search_service import MetaSearchService
from app.services.semantic_recall_service import (
    SemanticRecallService,
    SemanticRecallsNotFoundError,
)


def get_semantic_recall_context(
    runtime: ToolRuntime,
) -> tuple[int, UUID, SemanticRecallService]:
    """从工具运行时解析会话身份和召回管理服务"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("semantic recall context not found in config")
    if runtime.store is None:
        raise ValueError("semantic recall store is unavailable")
    return (
        user_id,
        UUID(raw_conversation_id),
        SemanticRecallService(SemanticRecallPGRepo(runtime.store)),
    )


@tool
async def search_semantic_resources(
    runtime: ToolRuntime,
    resource_types: Annotated[
        list[Literal["column", "metric", "value"]],
        "必须选择需要检索的资源类型：字段、指标或字段值，可多选",
    ],
    query: Annotated[str, "原始问题或需要检索的完整业务短语"],
    terms: Annotated[
        list[str] | None,
        "补充检索词或业务同义词，最多 20 个，不需要重复 query",
    ] = None,
    limit_per_type: Annotated[int, "每类直接候选的最大数量，范围 1 到 20"] = 5,
) -> dict[str, Any]:
    """检索字段、指标和字段值，保存并返回本次召回记录

    工具不会调用模型扩展关键词，也不会决定最终查询口径。第一次结果不充分时，
    可以调整 terms 或 resource_types 再次检索
    """
    try:
        request = SemanticSearchRequest(
            query=query,
            terms=terms or [],
            resource_types=resource_types,
            limit_per_type=limit_per_type,
        )
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "Invalid semantic search request",
            "details": exc.errors(include_url=False),
        }

    try:
        async with meta_postgres_client_manager.session() as meta_session:
            service = MetaSearchService(
                embedding_client=embedding_client_manager.get_client(),
                column_repo=ColumnESRepo(es_client_manager.get_client()),
                metric_repo=MetricESRepo(es_client_manager.get_client()),
                value_repo=ValueESRepo(es_client_manager.get_client()),
                meta_repo=MetaPGRepo(meta_session),
            )
            response = await service.search(request)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Metadata search failed: {type(exc).__name__}: {exc}",
        }

    try:
        user_id, conversation_id, recall_service = get_semantic_recall_context(runtime)
        record = await recall_service.record_search(
            user_id,
            conversation_id,
            request,
            response,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": (
                f"Semantic recall persistence failed: {type(exc).__name__}: {exc}"
            ),
        }

    result = response.model_dump(mode="json")
    result["recall_id"] = record.recall_id
    return result


def _record_summary(record: Any) -> dict[str, Any]:
    """构造适合模型浏览的召回记录摘要"""
    response = record.response
    return {
        "recall_id": record.recall_id,
        "kind": record.kind,
        "query": record.request.query if record.request is not None else None,
        "queries": response.queries,
        "source_recall_ids": record.source_recall_ids,
        "resource_counts": {
            "metrics": len(response.metrics),
            "columns": len(response.columns),
            "values": len(response.values),
            "tables": len(response.tables),
            "relations": len(response.relations),
        },
        "created_at": record.created_at.isoformat(),
    }


@tool
async def list_semantic_recalls(
    runtime: ToolRuntime,
    limit: Annotated[int, "返回最近记录的数量，范围 1 到 100"] = 20,
) -> dict[str, Any]:
    """列出当前会话已保存的语义召回记录及其查询和来源"""
    if not 1 <= limit <= 100:
        return {"status": "error", "message": "limit must be between 1 and 100"}
    try:
        user_id, conversation_id, service = get_semantic_recall_context(runtime)
        records = await service.list(user_id, conversation_id, limit=limit)
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "status": "success",
        "recalls": [_record_summary(record) for record in records],
    }


@tool
async def get_semantic_recall(
    runtime: ToolRuntime,
    recall_id: Annotated[str, "需要读取的召回记录 ID"],
) -> dict[str, Any]:
    """读取当前会话某条语义召回记录的请求、结果和合并来源"""
    try:
        user_id, conversation_id, service = get_semantic_recall_context(runtime)
        record = await service.get(user_id, conversation_id, recall_id)
    except SemanticRecallsNotFoundError as exc:
        return {
            "status": "error",
            "message": "semantic recall not found",
            "recall_ids": exc.recall_ids,
        }
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "success", "recall": record.model_dump(mode="json")}


@tool
async def merge_semantic_recalls(
    runtime: ToolRuntime,
    recall_ids: Annotated[
        list[str],
        "需要合并的召回记录 ID，至少两个且必须属于当前会话",
    ],
) -> dict[str, Any]:
    """去重合并多条语义召回并保存新快照，源记录保持不变"""
    try:
        user_id, conversation_id, service = get_semantic_recall_context(runtime)
        record = await service.merge(user_id, conversation_id, recall_ids)
    except SemanticRecallsNotFoundError as exc:
        return {
            "status": "error",
            "message": "semantic recalls not found",
            "recall_ids": exc.recall_ids,
        }
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "success", "recall": record.model_dump(mode="json")}


@tool
async def delete_semantic_recalls(
    runtime: ToolRuntime,
    recall_ids: Annotated[list[str], "需要删除的召回记录 ID"],
) -> dict[str, Any]:
    """删除当前会话指定的语义召回记录"""
    try:
        user_id, conversation_id, service = get_semantic_recall_context(runtime)
        deleted, missing = await service.delete(
            user_id,
            conversation_id,
            recall_ids,
        )
    except (ValueError, TypeError) as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "status": "success",
        "deleted_recall_ids": deleted,
        "missing_recall_ids": missing,
    }
