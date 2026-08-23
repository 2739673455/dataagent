"""Explorer 历史查询经验检索工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from loguru import logger

from app.analytics.agents.explorer.recall_runtime import resolve_semantic_recall_context
from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import (
    SemanticRecallService,
    SemanticRecallsNotFoundError,
)
from app.query.repositories.experience_index import QueryExperienceESRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.experience import QueryExperienceService
from app.shared.clients.embedding_client_manager import embedding_client_manager
from app.shared.clients.es_client_manager import es_client_manager
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg


@tool
async def search_query_experiences(
    runtime: ToolRuntime,
    query: Annotated[str, "当前数据问题或准备执行 SQL 的具体目的"],
    recall_ids: Annotated[
        list[str] | None,
        "当前问题已使用的语义召回 ID，可提高表字段匹配精度",
    ] = None,
    limit: Annotated[int, "返回经验数量，范围 1 到 10"] = 5,
) -> dict[str, Any]:
    """检索当前用户过去成功执行且仍满足最新权限的 SQL 模板"""
    query = query.strip()
    if not query or len(query) > 1000:
        return {
            "status": "error",
            "message": "查询文本长度必须在 1 到 1000 个字符之间",
        }
    if not 1 <= limit <= 10:
        return {"status": "error", "message": "limit 参数必须在 1 到 10 之间"}

    try:
        user_id, conversation_id, recall_repo = resolve_semantic_recall_context(
            runtime.config,
            runtime.store,
        )
        async with (
            auth_postgres_client_manager.session() as auth_session,
            meta_postgres_client_manager.session() as meta_session,
        ):
            auth_repo = AuthPGRepo(auth_session)
            user = await auth_repo.get_user_by_id(user_id)
            if user is None or user.doris_role_name is None:
                return {"status": "success", "experiences": []}
            policy = await AuthorizationService(auth_repo).get_asset_policy(user_id)
            authorization_filter = MetadataAuthorizationFilter(
                policy,
                cfg.query.data_source,
                cfg.doris.database,
            )
            recall_service = SemanticRecallService(recall_repo, authorization_filter)
            table_names: set[str] = set()
            column_keys: set[tuple[str, str]] = set()
            for recall_id in dict.fromkeys(recall_ids or []):
                record = await recall_service.get(
                    user_id,
                    conversation_id,
                    recall_id,
                )
                table_names.update(item.name for item in record.response.tables)
                column_keys.update(
                    (item.t_name, item.name) for item in record.response.columns
                )
            service = QueryExperienceService(
                repo=QueryExperiencePGRepo(session=meta_session),
                index_repo=QueryExperienceESRepo(client=es_client_manager.get_client()),
                embedding_client=embedding_client_manager.get_client(),
                data_source=cfg.query.data_source,
                database_name=cfg.doris.database,
            )
            experiences = await service.search(
                user_id=user_id,
                role_name=user.doris_role_name,
                policy=policy,
                query=query,
                table_names=table_names,
                column_keys=column_keys,
                limit=limit,
            )
    except SemanticRecallsNotFoundError as exc:
        return {
            "status": "error",
            "message": "未找到指定的语义召回记录",
            "recall_ids": exc.recall_ids,
        }
    except Exception:  # noqa: BLE001
        logger.exception("查询经验检索失败")
        return {
            "status": "error",
            "message": "查询经验检索暂不可用",
        }
    return {
        "status": "success",
        "experiences": [item.model_dump(mode="json") for item in experiences],
    }
