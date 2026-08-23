"""Explorer 语义资源召回与记录管理工具"""

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool
from loguru import logger
from pydantic import ValidationError

from app.analytics.agents.explorer.recall_runtime import (
    create_authorized_semantic_recall_service,
    resolve_semantic_recall_context,
)
from app.analytics.agents.explorer.semantic_recall_protocol import (
    semantic_recall_reference,
)
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.search_models import SemanticSearchRequest
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticRecallService,
    SemanticRecallsNotFoundError,
)
from app.metadata.services.search import MetaSearchService
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg


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
            "message": "语义检索请求无效",
            "details": exc.errors(include_url=False),
        }

    try:
        user_id, conversation_id, recall_repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        async with (
            auth_postgres_client_manager.session() as auth_session,
            meta_postgres_client_manager.session() as meta_session,
        ):
            asset_policy = await AuthorizationService(
                AuthPGRepo(auth_session)
            ).get_asset_policy(user_id)
            authorization_filter = MetadataAuthorizationFilter(
                asset_policy,
                cfg.query.data_source,
                cfg.doris.database,
            )
            service = MetaSearchService(
                embedding_client=embedding_client_manager.get_client(),
                column_repo=ColumnESRepo(es_client_manager.get_client()),
                metric_repo=MetricESRepo(es_client_manager.get_client()),
                value_repo=ValueESRepo(es_client_manager.get_client()),
                meta_repo=MetaPGRepo(meta_session),
                asset_policy=asset_policy,
                data_source=cfg.query.data_source,
                database_name=cfg.doris.database,
            )
            response = await service.search(request)
            recall_service = SemanticRecallService(
                recall_repo,
                authorization_filter,
            )
    except Exception:  # noqa: BLE001
        logger.exception("元数据语义检索失败")
        return {
            "status": "error",
            "message": "元数据检索服务暂不可用",
        }

    try:
        record = await recall_service.record_search(
            user_id,
            conversation_id,
            request,
            response,
        )
    except Exception:  # noqa: BLE001
        logger.exception("语义召回快照持久化失败")
        return {
            "status": "error",
            "message": "无法保存语义召回快照",
        }

    return semantic_recall_reference(record)


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
        return {"status": "error", "message": "limit 参数必须在 1 到 100 之间"}
    try:
        user_id, conversation_id, repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        service = await create_authorized_semantic_recall_service(user_id, repo)
        records = await service.list(user_id, conversation_id, limit=limit)
    except Exception:  # noqa: BLE001
        logger.exception("获取语义召回列表失败")
        return {
            "status": "error",
            "message": "语义召回列表暂不可用",
        }
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
        user_id, conversation_id, repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        service = await create_authorized_semantic_recall_service(user_id, repo)
        record = await service.get(user_id, conversation_id, recall_id)
    except SemanticRecallsNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到指定的语义召回记录",
            "recall_ids": exc.recall_ids,
        }
    except Exception:  # noqa: BLE001
        logger.exception("加载语义召回记录失败")
        return {
            "status": "error",
            "message": "语义召回记录暂不可用",
        }
    return semantic_recall_reference(record)


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
        user_id, conversation_id, repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        service = await create_authorized_semantic_recall_service(user_id, repo)
        record = await service.merge(user_id, conversation_id, recall_ids)
    except SemanticRecallsNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到待合并的语义召回记录",
            "recall_ids": exc.recall_ids,
        }
    except Exception:  # noqa: BLE001
        logger.exception("合并语义召回记录失败")
        return {
            "status": "error",
            "message": "无法合并语义召回记录",
        }
    return semantic_recall_reference(record)


@tool
async def delete_semantic_recalls(
    runtime: ToolRuntime,
    recall_ids: Annotated[list[str], "需要删除的召回记录 ID"],
) -> dict[str, Any]:
    """删除当前会话指定的语义召回记录"""
    try:
        user_id, conversation_id, repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        service = await create_authorized_semantic_recall_service(user_id, repo)
        deleted, missing = await service.delete(
            user_id,
            conversation_id,
            recall_ids,
        )
    except Exception:  # noqa: BLE001
        logger.exception("删除语义召回记录失败")
        return {
            "status": "error",
            "message": "无法删除语义召回记录",
        }
    return {
        "status": "success",
        "deleted_recall_ids": deleted,
        "missing_recall_ids": missing,
    }
