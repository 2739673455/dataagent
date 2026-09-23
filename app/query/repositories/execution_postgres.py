"""查询执行历史 PostgreSQL 数据访问。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.query.models.execution import QueryExecution


class QueryExecutionPGRepo:
    """持久化查询执行审计。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求使用的异步数据库会话。"""
        self._session = session

    async def record(self, execution: QueryExecution) -> None:
        """写入一次成功、拒绝或失败的 SQL 尝试。"""
        async with self._session.begin():
            self._session.add(execution)
            await self._session.flush()
