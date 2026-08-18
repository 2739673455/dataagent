"""应用数据实体注册入口"""

from app.entities import auth, meta
from app.entities.base import Base

__all__ = ["Base", "auth", "meta"]
