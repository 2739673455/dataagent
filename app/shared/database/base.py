"""应用关系型模型的声明基类。"""

from sqlalchemy.orm import DeclarativeBase


class AuthBase(DeclarativeBase):
    """认证与权限 ORM 声明基类。"""


class MetaBase(DeclarativeBase):
    """元数据 ORM 声明基类。"""


class AssistantBase(DeclarativeBase):
    """助手运行数据 ORM 声明基类。"""
