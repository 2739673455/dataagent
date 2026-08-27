"""Explorer 语义资源召回与记录管理工具"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool
from loguru import logger
from pydantic import ValidationError

from app.analytics.agents.explorer.recall_runtime import (
    create_authorized_semantic_recall_service,
    resolve_semantic_recall_identity,
    semantic_recall_repository,
)
from app.analytics.agents.explorer.semantic_recall_protocol import (
    semantic_recall_reference,
)
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.models.recall import normalize_semantic_recall_query
from app.metadata.models.search import SemanticResourceSearchRequest
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticQueriesNotFoundError,
    SemanticRecallService,
)
from app.metadata.services.search import MetaSearchService
from app.query.providers import build_query_experience_service
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.query_experience import QueryExperienceSearchResult

_QUERY_EXPERIENCE_LIMIT = 3


@tool
async def search_context(
    runtime: ToolRuntime,
    query: Annotated[str, "用于标识当前查询并检索历史 SQL 经验的完整数据问题"],
    resource_types: Annotated[
        list[Literal["column", "metric", "value"]],
        "必须选择需要检索的资源类型：字段、指标或字段值，可多选",
    ],
    terms: Annotated[
        list[str],
        "用于检索字段、指标和字段值的业务词或同义词，至少 1 个且最多 20 个",
    ],
    limit_per_type: Annotated[int, "每类直接候选的最大数量，范围 1 到 20"] = 5,
) -> dict[str, Any]:
    """检索语义资源和三条历史 SQL 经验，保存并返回本次召回记录

    query 只用于标识持续上下文和检索历史经验，terms 专门用于语义资源检索。
    同一 query 的多次检索会累积语义资源
    """
    try:
        query = normalize_semantic_recall_query(query)
        request = SemanticResourceSearchRequest(
            terms=terms,
            resource_types=resource_types,
            limit_per_type=limit_per_type,
        )
    except ValidationError as exc:
        return {
            "status": "error",
            "message": "语义检索请求无效",
            "details": exc.errors(include_url=False),
        }
    except ValueError as exc:
        return {
            "status": "error",
            "message": "语义检索请求无效",
            "details": [{"loc": ["query"], "msg": str(exc)}],
        }

    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with (
            auth_postgres_client_manager.session() as auth_session,
            meta_postgres_client_manager.session() as meta_search_session,
        ):
            auth_repo = AuthPGRepo(auth_session)
            user = await auth_repo.get_user_by_id(user_id)
            asset_policy = await AuthorizationService(auth_repo).get_asset_policy(
                user_id
            )
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
                meta_repo=MetaPGRepo(meta_search_session),
                asset_policy=asset_policy,
                data_source=cfg.query.data_source,
                database_name=cfg.doris.database,
            )
            response = await service.search(request)
        async with semantic_recall_repository() as recall_repo:
            recall_service = SemanticRecallService(recall_repo, authorization_filter)
            cached = await recall_service.get_fresh_query_experiences(
                user_id,
                conversation_id,
                query,
            )
        if cached is None:
            query_experiences: list[QueryExperienceSearchResult] = []
            if user is not None and user.doris_role_name is not None:
                async with meta_postgres_client_manager.session() as experience_session:
                    query_experiences = await build_query_experience_service(
                        experience_session
                    ).search(
                        user_id=user_id,
                        role_name=user.doris_role_name,
                        policy=asset_policy,
                        query=query,
                        table_names={item.name for item in response.tables},
                        column_keys={
                            (item.t_name, item.name) for item in response.columns
                        },
                        limit=_QUERY_EXPERIENCE_LIMIT,
                    )
            query_experiences_retrieved_at = datetime.now(UTC)
        else:
            query_experiences, query_experiences_retrieved_at = cached
    except Exception:  # noqa: BLE001
        logger.exception("语义资源与查询经验检索失败")
        return {
            "status": "error",
            "message": "语义资源与查询经验检索暂不可用",
        }

    try:
        async with semantic_recall_repository() as recall_repo:
            record = await SemanticRecallService(
                recall_repo,
                authorization_filter,
            ).record_search(
                user_id,
                conversation_id,
                query,
                request,
                response,
                query_experiences,
                query_experiences_retrieved_at,
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
        "query": record.query,
        "terms": response.terms,
        "source_queries": record.source_queries,
        "query_experience_count": len(record.query_experiences),
        "query_experiences_retrieved_at": (
            record.query_experiences_retrieved_at.isoformat()
        ),
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
async def list_recalls(
    runtime: ToolRuntime,
    limit: Annotated[int, "返回最近记录的数量，范围 1 到 100"] = 20,
) -> dict[str, Any]:
    """列出当前会话中每个 query 的最新召回记录"""
    if not 1 <= limit <= 100:
        return {"status": "error", "message": "limit 参数必须在 1 到 100 之间"}
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
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
async def get_recall(
    runtime: ToolRuntime,
    query: Annotated[str, "需要读取的查询业务键"],
) -> dict[str, Any]:
    """按 query 读取当前会话的最新召回记录"""
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
            service = await create_authorized_semantic_recall_service(user_id, repo)
            record = await service.get(user_id, conversation_id, query)
    except SemanticQueriesNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到指定的语义召回记录",
            "queries": exc.queries,
        }
    except Exception:  # noqa: BLE001
        logger.exception("加载语义召回记录失败")
        return {
            "status": "error",
            "message": "语义召回记录暂不可用",
        }
    return semantic_recall_reference(record)


@tool
async def merge_recalls(
    runtime: ToolRuntime,
    target_query: Annotated[str, "接收元数据并保留的目标 query"],
    source_query: Annotated[str, "提供元数据并在合并后删除的来源 query"],
) -> dict[str, Any]:
    """合并来源 query 的语义资源并删除来源，查询经验只保留目标结果"""
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
            service = await create_authorized_semantic_recall_service(user_id, repo)
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
    except Exception:  # noqa: BLE001
        logger.exception("合并语义召回记录失败")
        return {
            "status": "error",
            "message": "无法合并语义召回记录",
        }
    return semantic_recall_reference(record)


@tool
async def delete_recalls(
    runtime: ToolRuntime,
    queries: Annotated[list[str], "需要删除的查询业务键"],
) -> dict[str, Any]:
    """删除当前会话指定 query 的全部召回快照"""
    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
            service = await create_authorized_semantic_recall_service(user_id, repo)
            deleted, missing = await service.delete(
                user_id,
                conversation_id,
                queries,
            )
    except Exception:  # noqa: BLE001
        logger.exception("删除语义召回记录失败")
        return {
            "status": "error",
            "message": "无法删除语义召回记录",
        }
    return {
        "status": "success",
        "deleted_queries": deleted,
        "missing_queries": missing,
    }
