"""受控 PostgreSQL 业务库分析查询访问。"""

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.query.contracts import QueryBatch, QueryExecutionLimits, QueryExecutionOptions
from app.query.errors import QueryExecutionTimeoutError
from app.shared.clients.business_pg_client_manager import BusinessPgClientManager


class BusinessPgQueryRepository:
    """在独立查询身份的只读事务中以服务端游标分批读取。"""

    def __init__(
        self, connection_provider: BusinessPgClientManager, *, schema_name: str
    ) -> None:
        self._connection_provider = connection_provider
        self._schema_name = schema_name

    async def stream(
        self,
        sql: str,
        limits: QueryExecutionLimits,
        options: QueryExecutionOptions,
    ) -> AsyncGenerator[QueryBatch]:
        """整个事务的读权限由 PostgreSQL 的角色、GRANT 与 RLS 强制执行。"""
        async with self._connection_provider.engine.connect() as connection:
            try:
                async with connection.begin():
                    await self._apply_transaction_limits(connection, limits)
                    # 转义 SQLAlchemy 的冒号绑定语法；驱动仍按规则处理字面百分号。
                    async with connection.stream(
                        text(sql.replace(":", r"\:")),
                        execution_options={"yield_per": options.batch_size},
                    ) as result:
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
            except asyncio.CancelledError:
                await connection.invalidate()
                raise
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "57014":
                    raise QueryExecutionTimeoutError(
                        f"PostgreSQL 查询执行超时，最大允许 {limits.timeout_seconds} 秒"
                    ) from exc
                raise

    async def _apply_transaction_limits(
        self, connection: AsyncConnection, limits: QueryExecutionLimits
    ) -> None:
        """设置事务局部限制；连接归还后不影响其他查询。"""
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(
            text(
                "SELECT set_config('statement_timeout', :timeout, true), "
                "set_config('work_mem', :work_mem, true), "
                "set_config('search_path', quote_ident(:schema_name), true), "
                "set_config('row_security', 'on', true)"
            ),
            {
                "timeout": f"{limits.timeout_seconds}s",
                "work_mem": f"{limits.work_mem_kb}kB",
                "schema_name": self._schema_name,
            },
        )
