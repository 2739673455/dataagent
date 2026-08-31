"""Explorer Agent 语义召回运行时依赖"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.identity.repositories.auth import AuthPGRepo
from app.identity.services.authorization import AuthorizationService
from app.metadata.repositories.recall import SemanticRecallPGRepo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.metadata.services.recall import SemanticRecallContextService
from app.shared.clients.postgres_client_manager import (
    auth_postgres_client_manager,
    meta_postgres_client_manager,
)
from app.shared.config.app_config import cfg


def resolve_semantic_recall_identity(
    config: RunnableConfig,
) -> tuple[int, UUID]:
    """从服务端运行配置解析会话身份"""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    raw_conversation_id = configurable.get("conversation_id")
    if not isinstance(user_id, int) or not isinstance(raw_conversation_id, str):
        raise TypeError("配置中未找到语义召回上下文")
    return user_id, UUID(raw_conversation_id)


@asynccontextmanager
async def semantic_recall_repository() -> AsyncGenerator[SemanticRecallPGRepo]:
    """创建带短事务边界的语义召回数据访问"""
    async with (
        meta_postgres_client_manager.session() as session,
        session.begin(),
    ):
        yield SemanticRecallPGRepo(session)


async def create_authorized_semantic_recall_service(
    user_id: int,
    repo: SemanticRecallPGRepo,
) -> SemanticRecallContextService:
    """使用用户最新资产权限创建召回服务"""
    async with auth_postgres_client_manager.session() as auth_session:
        policy = await AuthorizationService(AuthPGRepo(auth_session)).get_asset_policy(
            user_id
        )
    return SemanticRecallContextService(
        repo,
        MetadataAuthorizationFilter(
            policy,
            cfg.query.data_source,
            cfg.doris.database,
        ),
        query_experience_role_name=policy.role_name,
        query_experience_authorization_epoch=policy.authorization_epoch,
    )
