"""查询执行历史 PostgreSQL 数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.query.models.execution import QueryExecution


class QueryExecutionPGRepo:
    """持久化查询执行审计并读取经验来源执行。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求使用的异步数据库会话。"""
        self._session = session

    async def record(self, execution: QueryExecution) -> None:
        """写入一次成功、拒绝或失败的 SQL 尝试。"""
        self._session.add(execution)
        await self._session.flush()

    async def list_source_executions(
        self,
        experience_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[QueryExecution], int]:
        """分页读取一条经验的成功来源执行记录。"""
        filters = (
            QueryExecution.experience_id == experience_id,
            QueryExecution.status == "succeeded",
        )
        total = await self._session.scalar(
            select(func.count()).select_from(QueryExecution).where(*filters)
        )
        executions = list(
            (
                await self._session.scalars(
                    select(QueryExecution)
                    .where(*filters)
                    .order_by(
                        QueryExecution.created_at.desc(), QueryExecution.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return executions, total or 0
