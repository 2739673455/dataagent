"""受控 Doris 分析查询访问。"""

import asyncio
import re
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.query.errors import QueryExecutionTimeoutError, QueryPermissionDeniedError
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
        try:
            async with self._connection_provider.engine.connect() as connection:
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
            if _is_permission_denied(exc):
                raise QueryPermissionDeniedError(
                    "当前账号没有执行该查询所需的数据访问权限"
                ) from exc
            message = str(exc).lower()
            if (
                isinstance(exc, TimeoutError)
                or "timeout" in message
                or "timed out" in message
            ):
                raise QueryExecutionTimeoutError("Doris 查询执行超时") from exc
            raise


# Doris ErrorCode 中的数据访问错误；1045 等登录认证错误不属于数据授权不足。
_PERMISSION_DENIED_CODES = frozenset(
    {1044, 1142, 1143, 1221, 1222, 1223, 1224, 1225, 1227, 5087}
)
_PERMISSION_MESSAGE = re.compile(
    r"^(?:errCode\s*=\s*\d+,\s*detailMessage\s*=\s*)?"
    r"(?:SELECT command denied to user\b|"
    r"Permission denied: user .+ does not have privilege for\b|"
    r"Access denied; you need\b|"
    r"Access denied for user .+ to (?:database|catalog)\b)",
    re.IGNORECASE,
)


def _is_permission_denied(error: SQLAlchemyError | TimeoutError) -> bool:
    """识别驱动错误码及被 Doris 通用错误码包装的授权拒绝。"""
    if not isinstance(error, DBAPIError) or error.orig is None:
        return False
    args = error.orig.args
    if not args:
        return False
    if args[0] in _PERMISSION_DENIED_CODES:
        return True
    return (
        args[0] == 1105
        and len(args) > 1
        and isinstance(args[1], str)
        and _PERMISSION_MESSAGE.match(args[1]) is not None
    )
