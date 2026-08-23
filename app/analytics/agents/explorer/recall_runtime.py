"""Explorer Agent 语义召回运行时上下文"""

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore

from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import SemanticRecallService
from app.shared.clients.postgres_client_manager import auth_postgres_client_manager
from app.shared.config.app_config import cfg


def resolve_semantic_recall_context(
    config: RunnableConfig,
    store: BaseStore | None,
) -> tuple[int, UUID, SemanticRecallPGRepo]:
    """从服务端运行配置解析会话身份和召回存储"""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("配置中未找到语义召回上下文")
    if store is None:
        raise ValueError("语义召回存储服务不可用")
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
    async with auth_postgres_client_manager.session() as auth_session:
        policy = await AuthorizationService(AuthPGRepo(auth_session)).get_asset_policy(
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
