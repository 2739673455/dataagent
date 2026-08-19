"""应用数据模型注册入口"""

from app.models import auth, meta
from app.models.base import AuthBase, MetaBase
from app.models.conversation import ConversationInfo
from app.models.semantic_recall import SemanticRecallRecord

__all__ = [
    "AuthBase",
    "ConversationInfo",
    "MetaBase",
    "SemanticRecallRecord",
    "auth",
    "meta",
]
