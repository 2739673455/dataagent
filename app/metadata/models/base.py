"""元数据目录模型的独立注册表。"""

from sqlalchemy.orm import DeclarativeBase


class MetaBase(DeclarativeBase):
    """元数据目录 ORM 声明基类。"""
