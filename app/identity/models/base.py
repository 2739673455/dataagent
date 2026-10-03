"""认证与权限模型的独立注册表。"""

from sqlalchemy.orm import DeclarativeBase


class AuthBase(DeclarativeBase):
    """认证与权限 ORM 声明基类。"""
