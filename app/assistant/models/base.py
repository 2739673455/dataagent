"""会话与召回模型的独立注册表。"""

from sqlalchemy.orm import DeclarativeBase


class AssistantBase(DeclarativeBase):
    """会话与召回 ORM 声明基类。"""
