"""聊天接口依赖"""

from typing import Annotated

from fastapi import Depends

from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.repositories.conversation_pg_repo import ConversationPGRepo
from app.repositories.semantic_recall_pg_repo import SemanticRecallPGRepo


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
