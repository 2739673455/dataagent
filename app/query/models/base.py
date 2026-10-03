"""查询执行与经验模型的独立注册表。"""

from sqlalchemy.orm import DeclarativeBase


class QueryBase(DeclarativeBase):
    """查询执行与经验 ORM 声明基类。"""
