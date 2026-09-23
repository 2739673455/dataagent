"""受控 Doris 分析查询访问。"""

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.query.errors import QueryExecutionTimeoutError
from app.query.models.execution import (
    QueryBatch,
    QueryExecutionOptions,
)
from app.shared.clients.doris_client_manager import DorisClientManager


class DorisQueryRepository:
    """使用服务端游标分批读取 Doris 查询结果。"""

    def __init__(self, connection_provider: DorisClientManager) -> None:
        """初始化 Doris 查询存储。"""
        self._connection_provider = connection_provider

    async def stream(
        self,
        sql: str,
        options: QueryExecutionOptions,
    ) -> AsyncGenerator[QueryBatch]:
        """流式返回查询结果分区。"""
        async with self._connection_provider.connection() as connection:
            try:
                result = await connection.stream(
                    # 避免将 SQL 字符串中的冒号识别为绑定参数。
                    text(sql.replace(":", r"\:")),
                    execution_options={
                        "stream_results": True,
                        "yield_per": options.batch_size,
                    },
                )
                try:
                    column_names = tuple(map(str, result.keys()))
                    yielded = False
                    async for rows in result.partitions(options.batch_size):
                        yielded = True
                        yield QueryBatch(
                            column_names=column_names,
                            rows=tuple(tuple(row) for row in rows),
                        )
                    if not yielded:
                        yield QueryBatch(column_names=column_names, rows=())
                finally:
                    await result.close()
            except asyncio.CancelledError:
                await connection.invalidate()
                raise
            except (SQLAlchemyError, TimeoutError) as exc:
                message = str(exc).lower()
                if (
                    isinstance(exc, TimeoutError)
                    or "timeout" in message
                    or "timed out" in message
                ):
                    raise QueryExecutionTimeoutError("Doris 查询执行超时") from exc
                raise
