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
from app.analytics.agents.explorer.semantic_recall_middleware import (
    semantic_recall_payload,
)
from app.analytics.agents.explorer.semantic_recall_protocol import (
    semantic_recall_reference,
)
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.models.recall import (
    SemanticRecallResourceDeletion,
    normalize_semantic_recall_query,
)
from app.metadata.models.search import SemanticResourceRecallRequest
from app.metadata.repositories.column_index import ColumnESRepo
from app.metadata.repositories.metric_index import MetricESRepo
from app.metadata.repositories.postgres import MetaPGRepo
from app.metadata.repositories.value_index import ValueESRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticQueriesNotFoundError,
    SemanticRecallContextService,
)
from app.metadata.services.search import SemanticResourceRecallService
from app.query.providers import build_query_experience_service
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg
from app.shared.contracts.query_experience import (
    QUERY_EXPERIENCE_RECALL_LIMIT,
    QueryExperienceRecallResult,
)

_STALE_QUERY_EXPERIENCES_RETRIEVED_AT = datetime.min.replace(tzinfo=UTC)


def _invalid_query_response(
    location: list[str | int],
    error: ValueError,
    *,
    message: str,
) -> dict[str, Any]:
    """构造 query 业务键校验失败的工具响应"""
    return {
        "status": "error",
        "message": message,
        "details": [{"loc": location, "msg": str(error)}],
    }


def _tool_error_response(
    message: str,
    error: Exception,
) -> dict[str, Any]:
    """构造包含异常类别和原因的工具错误响应"""
    detail = str(error).strip() or "异常未提供详情"
    return {
        "status": "error",
        "message": message,
        "details": [{"type": type(error).__name__, "msg": detail}],
    }


@tool
async def recall_context(
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

    query 只用于标识持续上下文和检索历史经验，terms 专门用于语义资源检索
    同一 query 的多次检索会累积语义资源
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
            "message": "删除请求无效",
            "details": exc.errors(include_url=False),
        }
    except ValueError as exc:
        return _invalid_query_response(
            ["query"],
            exc,
            message="语义召回请求无效",
        )

    try:
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with (
            auth_postgres_client_manager.session() as auth_session,
            meta_postgres_client_manager.session() as meta_search_session,
        ):
            auth_repo = AuthPGRepo(auth_session)
            asset_policy = await AuthorizationService(auth_repo).get_asset_policy(
                user_id
            )
            authorization_filter = MetadataAuthorizationFilter(
                asset_policy,
                cfg.query.data_source,
                cfg.doris.database,
            )
            service = SemanticResourceRecallService(
                embedding_client=embedding_client_manager.get_client(),
                column_repo=ColumnESRepo(es_client_manager.get_client()),
                metric_repo=MetricESRepo(es_client_manager.get_client()),
                value_repo=ValueESRepo(es_client_manager.get_client()),
                meta_repo=MetaPGRepo(meta_search_session),
                asset_policy=asset_policy,
                data_source=cfg.query.data_source,
                database_name=cfg.doris.database,
            )
            response = await service.recall(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("语义资源召回失败")
        return _tool_error_response("语义资源召回失败", exc)

    query_experiences: list[QueryExperienceRecallResult] = []
    query_experiences_retrieved_at = _STALE_QUERY_EXPERIENCES_RETRIEVED_AT
    try:
        async with semantic_recall_repository() as recall_repo:
            recall_service = SemanticRecallContextService(
                recall_repo,
                authorization_filter,
                query_experience_role_name=asset_policy.role_name,
                query_experience_authorization_epoch=(
                    asset_policy.authorization_epoch
                ),
            )
            cached = await recall_service.get_fresh_query_experiences(
                user_id,
                conversation_id,
                query,
                role_name=asset_policy.role_name,
                authorization_epoch=asset_policy.authorization_epoch,
            )
        if cached is None:
            if (
                asset_policy.role_name is not None
                and asset_policy.authorization_epoch is not None
            ):
                async with meta_postgres_client_manager.session() as experience_session:
                    experience_recall = await build_query_experience_service(
                        experience_session
                    ).recall(
                        role_name=asset_policy.role_name,
                        authorization_epoch=asset_policy.authorization_epoch,
                        policy=asset_policy,
                        query=query,
                        limit=QUERY_EXPERIENCE_RECALL_LIMIT,
                    )
                    if experience_recall.status == "failed":
                        logger.warning("查询经验全文和向量检索均不可用")
                    else:
                        query_experiences = experience_recall.results
                        query_experiences_retrieved_at = datetime.now(UTC)
            else:
                query_experiences_retrieved_at = datetime.now(UTC)
        else:
            query_experiences, query_experiences_retrieved_at = cached
    except Exception:  # noqa: BLE001
        logger.exception("查询经验检索失败")

    try:
        async with semantic_recall_repository() as recall_repo:
            record = await SemanticRecallContextService(
                recall_repo,
                authorization_filter,
                query_experience_role_name=asset_policy.role_name,
                query_experience_authorization_epoch=(
                    asset_policy.authorization_epoch
                ),
            ).record(
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


def _record_summary(record: Any) -> dict[str, Any]:
    """构造供后续 get_recall 使用的最小记录引用"""
    return {"query": record.query}


@tool
async def list_recalls(
    runtime: ToolRuntime,
    limit: Annotated[int, "返回最近记录的数量，范围 1 到 100"] = 20,
) -> dict[str, Any]:
    """列出当前会话中每个 query 的最新召回记录"""
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
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
            service = await create_authorized_semantic_recall_service(user_id, repo)
            records = await service.list(user_id, conversation_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("获取语义召回列表失败")
        return _tool_error_response("获取语义召回列表失败", exc)
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
        query = normalize_semantic_recall_query(query)
    except ValueError as exc:
        return _invalid_query_response(
            ["query"],
            exc,
            message="语义召回请求无效",
        )
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("加载语义召回记录失败")
        return _tool_error_response("加载语义召回记录失败", exc)
    return semantic_recall_reference(record)


@tool
async def merge_recalls(
    runtime: ToolRuntime,
    target_query: Annotated[str, "接收元数据并保留的目标 query"],
    source_query: Annotated[str, "提供元数据并在合并后删除的来源 query"],
) -> dict[str, Any]:
    """合并来源 query 的语义资源并删除来源，查询经验只保留目标结果"""
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("合并语义召回记录失败")
        return _tool_error_response("无法合并语义召回记录", exc)
    return semantic_recall_reference(record)


@tool
async def delete_recalls(
    runtime: ToolRuntime,
    deletions: Annotated[
        list[SemanticRecallResourceDeletion],
        (
            "待删除的 query 上下文树。tables 结构为表名到 columns 再到字段值；"
            "metrics 为指标名称映射；query_experiences 的每项仅包含经验 ID。"
            "未提供资源选择器时删除整个 query。"
        ),
    ],
) -> dict[str, Any]:
    """删除当前会话 query 的全部上下文或其中指定资源"""
    if not deletions:
        return {
            "status": "error",
            "message": "删除请求无效",
            "details": [{"loc": ["deletions"], "msg": "至少需要一个删除项"}],
        }

    normalized_deletions: list[SemanticRecallResourceDeletion] = []
    seen_queries: set[str] = set()
    for index, deletion in enumerate(deletions):
        try:
            deletion = SemanticRecallResourceDeletion.model_validate(deletion)
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
        user_id, conversation_id = resolve_semantic_recall_identity(runtime.config)
        async with semantic_recall_repository() as repo:
            service = await create_authorized_semantic_recall_service(user_id, repo)
            records = await service.delete(
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
    return {
        "status": "success",
        "recalls": [semantic_recall_payload(record) for record in records],
    }
