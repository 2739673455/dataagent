"""受控 Doris 分析查询访问。"""

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.query.errors import QueryExecutionTimeoutError
from app.query.models.execution import (
    QueryBatch,
    QueryExecutionLimits,
    QueryExecutionOptions,
)
from app.shared.clients.doris_client_manager import DorisClientManager


class DorisQueryRepository:
    """使用服务端游标分批读取 Doris 查询结果。"""

    def __init__(self, connection_provider: DorisClientManager) -> None:
        """初始化 Doris 查询存储。"""
        self._connection_provider = connection_provider

    @staticmethod
    async def _apply_session_limits(
        connection: AsyncConnection,
        limits: QueryExecutionLimits,
    ) -> None:
        """设置当前连接的 Doris 查询资源限制。"""
        await connection.execute(
            text(f"SET workload_group = '{limits.workload_group}'")
        )
        await connection.execute(text(f"SET query_timeout = {limits.timeout_seconds}"))
        await connection.execute(
            text(f"SET exec_mem_limit = {limits.memory_limit_bytes}")
        )

    async def stream(
        self,
        sql: str,
        limits: QueryExecutionLimits,
        options: QueryExecutionOptions,
    ) -> AsyncGenerator[QueryBatch]:
        """设置会话限制并流式返回查询结果分区。"""
        async with self._connection_provider.connection() as connection:
            try:
                await self._apply_session_limits(connection, limits)
                result = await connection.stream(
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
                    raise QueryExecutionTimeoutError(
                        f"Doris 查询执行超时，最大允许 {limits.timeout_seconds} 秒"
                    ) from exc
                raise
