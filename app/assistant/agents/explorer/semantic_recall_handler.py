"""Explorer 语义资源召回与记录管理用例。"""

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import ValidationError

from app.assistant.agents.explorer.recall_runtime import (
    SemanticRecallRuntime,
    resolve_semantic_recall_identity,
)
from app.assistant.agents.explorer.semantic_recall_protocol import (
    semantic_recall_deletion_result,
    semantic_recall_reference,
)
from app.metadata.errors import SemanticQueriesNotFoundError
from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
    normalize_semantic_recall_query,
)
from app.metadata.models.search import SemanticResourceRecallRequest


def _invalid_query_response(
    location: list[str | int],
    error: ValueError,
    *,
    message: str,
) -> dict[str, Any]:
    """构造 query 业务键校验失败的工具响应。"""
    return {
        "status": "error",
        "message": message,
        "details": [{"loc": location, "msg": str(error)}],
    }


def _tool_error_response(
    message: str,
    error: Exception,
) -> dict[str, Any]:
    """构造包含异常类别和原因的工具错误响应。"""
    detail = str(error).strip() or "异常未提供详情"
    return {
        "status": "error",
        "message": message,
        "details": [{"type": type(error).__name__, "msg": detail}],
    }


async def recall_context(
    config: RunnableConfig,
    query: str,
    resource_types: list[Literal["column", "metric", "value"]],
    terms: list[str],
    limit_per_type: int,
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """按稳定 query 业务键累计召回语义资源，并检索三条历史 SQL 经验

    一个 query 在当前会话内对应一个持续召回上下文。同一数据任务的后续调用原样
    复用 query，新的 terms 和 resource_types 召回结果会合入该上下文的已有结果。
    terms 仅描述本次需要补充检索的字段、指标或字段值。
    """
    try:
        query = normalize_semantic_recall_query(query)
        request = SemanticResourceRecallRequest(
            terms=terms,
            resource_types=resource_types,
            limit_per_type=limit_per_type,
        )
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "语义召回请求无效",
            "details": exc.errors(include_url=False),
        }
    except ValueError as exc:
        return _invalid_query_response(
            ["query"],
            exc,
            message="语义召回请求无效",
        )

    try:
        user_id, conversation_id = resolve_semantic_recall_identity(config)
        asset_policy, response = await recall.search(user_id, request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("语义资源召回失败")
        return _tool_error_response("语义资源召回失败", exc)

    query_experiences, query_experiences_retrieved_at = await recall.query_experiences(
        user_id, conversation_id, query, asset_policy
    )
    try:
        async with recall.context_service(user_id, policy=asset_policy) as service:
            record = await service.record(
                user_id,
                conversation_id,
                query,
                request,
                response,
                query_experiences,
                query_experiences_retrieved_at,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("语义召回快照持久化失败")
        return _tool_error_response(
            "无法保存语义召回快照",
            exc,
        )

    return semantic_recall_reference(record)


def _record_summary(record: SemanticRecallRecord) -> dict[str, Any]:
    """构造供后续 get_recall 使用的最小记录引用。"""
    return {
        "query": record.query,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


async def list_recalls(
    config: RunnableConfig,
    limit: int,
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """列出当前会话中每个 query 业务键对应的最新累计召回记录。"""
    if not 1 <= limit <= 100:
        return {
            "status": "error",
            "message": "语义召回请求无效",
            "details": [
                {
                    "loc": ["limit"],
                    "msg": "limit 参数必须在 1 到 100 之间",
                }
            ],
        }
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(config)
        async with recall.context_service(user_id) as service:
            records = await service.list(user_id, conversation_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("获取语义召回列表失败")
        return _tool_error_response("获取语义召回列表失败", exc)
    return {
        "status": "success",
        "recalls": [_record_summary(record) for record in records],
    }


async def get_recall(
    config: RunnableConfig,
    query: str,
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """按 query 业务键读取当前会话的最新累计召回记录。"""
    try:
        query = normalize_semantic_recall_query(query)
    except ValueError as exc:
        return _invalid_query_response(
            ["query"],
            exc,
            message="语义召回请求无效",
        )
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(config)
        async with recall.context_service(user_id) as service:
            record = await service.get(user_id, conversation_id, query)
    except SemanticQueriesNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到指定的语义召回记录",
            "queries": exc.queries,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("加载语义召回记录失败")
        return _tool_error_response("加载语义召回记录失败", exc)
    return semantic_recall_reference(record)


async def merge_recalls(
    config: RunnableConfig,
    target_query: str,
    source_query: str,
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """合并来源 query 的语义资源并删除来源，查询经验只保留目标结果。"""
    try:
        target_query = normalize_semantic_recall_query(target_query)
    except ValueError as exc:
        return _invalid_query_response(
            ["target_query"],
            exc,
            message="语义召回请求无效",
        )
    try:
        source_query = normalize_semantic_recall_query(source_query)
    except ValueError as exc:
        return _invalid_query_response(
            ["source_query"],
            exc,
            message="语义召回请求无效",
        )
    if target_query == source_query:
        return _invalid_query_response(
            ["source_query"],
            ValueError("目标 query 和来源 query 不能相同"),
            message="语义召回请求无效",
        )
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(config)
        async with recall.context_service(user_id) as service:
            record = await service.merge(
                user_id,
                conversation_id,
                target_query,
                source_query,
            )
    except SemanticQueriesNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到待合并的语义召回记录",
            "queries": exc.queries,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("合并语义召回记录失败")
        return _tool_error_response("无法合并语义召回记录", exc)
    return semantic_recall_reference(record)


async def delete_recalls(
    config: RunnableConfig,
    deletions: list[SemanticRecallResourceDeletion],
    *,
    recall: SemanticRecallRuntime,
) -> dict[str, Any]:
    """删除当前会话 query 的全部上下文或其中指定资源。"""
    if not deletions:
        return {
            "status": "error",
            "message": "删除请求无效",
            "details": [{"loc": ["deletions"], "msg": "至少需要一个删除项"}],
        }

    normalized_deletions: list[SemanticRecallResourceDeletion] = []
    seen_queries: set[str] = set()
    for index, raw_deletion in enumerate(deletions):
        try:
            deletion = SemanticRecallResourceDeletion.model_validate(raw_deletion)
        except ValidationError as exc:
            details: list[dict[str, Any]] = []
            for detail in exc.errors(include_url=False):
                item = dict(detail)
                item["loc"] = ["deletions", index, *detail["loc"]]
                details.append(item)
            return {
                "status": "error",
                "message": "删除请求无效",
                "details": details,
            }
        try:
            query = normalize_semantic_recall_query(deletion.query)
        except ValueError as exc:
            return _invalid_query_response(
                ["deletions", index, "query"],
                exc,
                message="删除请求无效",
            )
        if query in seen_queries:
            return _invalid_query_response(
                ["deletions", index, "query"],
                ValueError("同一 query 只能出现一次"),
                message="删除请求无效",
            )
        seen_queries.add(query)
        normalized_deletions.append(deletion.model_copy(update={"query": query}))
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(config)
        async with recall.context_service(user_id) as service:
            await service.delete(
                user_id,
                conversation_id,
                normalized_deletions,
            )
    except SemanticQueriesNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到待删除的语义召回记录",
            "queries": exc.queries,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("删除语义召回记录失败")
        return _tool_error_response("无法删除语义召回记录", exc)
    return semantic_recall_deletion_result(normalized_deletions)
