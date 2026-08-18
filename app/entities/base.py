"""应用关系型实体的统一声明基类"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """应用 ORM 声明基类"""
