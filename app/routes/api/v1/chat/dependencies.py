"""聊天接口依赖"""

from typing import Annotated

from fastapi import Depends

from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.conf.app_config import cfg
from app.repositories.conversation_pg_repo import ConversationPGRepo
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo
from app.routes.api.v1.auth.dependencies import (
    AuthorizationServiceDep,
    CurrentUserDep,
)
from app.services.metadata_authorization_filter import MetadataAuthorizationFilter
from app.services.semantic_recall_service import SemanticRecallService


def get_conversation_pg_repo() -> ConversationPGRepo:
    """创建会话目录数据访问"""
    return ConversationPGRepo(langgraph_postgres_manager.get_store())


ConversationPGRepoDep = Annotated[
    ConversationPGRepo,
    Depends(get_conversation_pg_repo),
]


def get_semantic_recall_pg_repo() -> SemanticRecallPGRepo:
    """创建语义召回记录数据访问"""
    return SemanticRecallPGRepo(langgraph_postgres_manager.get_store())


SemanticRecallPGRepoDep = Annotated[
    SemanticRecallPGRepo,
    Depends(get_semantic_recall_pg_repo),
]


async def get_semantic_recall_service(
    repo: SemanticRecallPGRepoDep,
    current_user: CurrentUserDep,
    authorization_service: AuthorizationServiceDep,
) -> SemanticRecallService:
    """创建应用当前资产策略的召回管理服务"""
    policy = await authorization_service.get_asset_policy(current_user.id)
    return SemanticRecallService(
        repo,
        MetadataAuthorizationFilter(
            policy,
            cfg.query.data_source,
            cfg.doris.database,
        ),
    )


SemanticRecallServiceDep = Annotated[
    SemanticRecallService,
    Depends(get_semantic_recall_service),
]
