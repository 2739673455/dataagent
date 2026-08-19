"""Explorer Agent 语义召回运行时上下文"""

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore

from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.app_config import cfg
from app.repositories.auth_pg_repo import AuthPGRepo
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.services.authorization_service import AuthorizationService
from app.services.metadata_authorization_filter import MetadataAuthorizationFilter
from app.services.semantic_recall_service import SemanticRecallService


def resolve_semantic_recall_context(
    config: RunnableConfig,
    store: BaseStore | None,
) -> tuple[int, UUID, SemanticRecallPGRepo]:
    """从服务端运行配置解析会话身份和召回存储"""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("semantic recall context not found in config")
    if store is None:
        raise ValueError("semantic recall store is unavailable")
    return (
        user_id,
        UUID(raw_conversation_id),
        SemanticRecallPGRepo(store),
    )


async def create_authorized_semantic_recall_service(
    user_id: int,
    repo: SemanticRecallPGRepo,
) -> SemanticRecallService:
    """使用用户最新资产权限创建召回服务"""
    async with meta_postgres_client_manager.session() as meta_session:
        policy = await AuthorizationService(AuthPGRepo(meta_session)).get_asset_policy(
            user_id
        )
    return SemanticRecallService(
        repo,
        MetadataAuthorizationFilter(
            policy,
            cfg.query.data_source,
            cfg.doris.database,
        ),
    )
