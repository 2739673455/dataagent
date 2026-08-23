"""受控 Doris 分析查询访问"""

import asyncio
import inspect
import re
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.query.models import QueryBatch, QueryExecutionLimits

_PRIVILEGE_PATTERN = re.compile(
    r"\b(?:node|admin|grant|select|load|alter|create|drop|usage|show_view)_priv\b"
    r"|\b(?:all|read_write|read_only)\b",
    re.IGNORECASE,
)
_ALLOWED_READONLY_PRIVILEGES = {
    "select_priv",
    "usage_priv",
    "show_view_priv",
    "read_only",
}


class DorisReadonlyPrivilegeError(RuntimeError):
    """Doris 查询账号包含写入或管理权限"""


class DorisConnectionProvider(Protocol):
    """按查询创建 Doris 异步连接的最小接口"""

    def connection(self) -> AsyncConnection: ...


class DorisQueryRepository:
    """使用服务端游标分批读取 Doris 查询结果"""

    def __init__(self, connection_provider: DorisConnectionProvider) -> None:
        """初始化 Doris 查询存储"""
        self._connection_provider = connection_provider

    @staticmethod
    async def _apply_session_limits(
        connection: AsyncConnection,
        limits: QueryExecutionLimits,
    ) -> None:
        """设置当前连接的 Doris 查询资源限制"""
        await connection.execute(
            text(f"SET workload_group = '{limits.workload_group}'")
        )
        await connection.execute(text(f"SET query_timeout = {limits.timeout_seconds}"))
        await connection.execute(
            text(f"SET exec_mem_limit = {limits.memory_limit_bytes}")
        )
        await connection.execute(
            text(f"SET max_allowed_packet = {limits.max_cell_bytes}")
        )

    async def verify_readonly_access(
        self,
        workload_group: str,
        database: str,
        expected_role: str,
    ) -> None:
        """启动前确认查询账号仅绑定预期角色且可见目标数据库"""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workload_group) is None:
            raise ValueError("Doris Workload Group 标识无效")
        if not database.strip():
            raise ValueError("Doris 查询数据库名不能为空")
        async with self._connection_provider.connection() as connection:
            result = await connection.execute(text("SHOW GRANTS"))
            rows = [dict(row) for row in result.mappings().all()]
            database_result = await connection.execute(
                text("SHOW DATABASES LIKE :database"),
                {"database": database},
            )
            visible_databases = {
                str(row[0]) for row in database_result.fetchall() if row
            }
            await connection.execute(text(f"SET workload_group = '{workload_group}'"))
        self.require_readonly_grants(rows, expected_role)
        if database not in visible_databases:
            raise DorisReadonlyPrivilegeError(
                "Doris 查询账号无权访问所配置的目标数据库"
            )

    @staticmethod
    def require_readonly_grants(
        rows: Sequence[Mapping[str, object]],
        expected_role: str,
    ) -> None:
        """校验 SHOW GRANTS 返回的当前账号合并权限"""
        if not rows:
            raise DorisReadonlyPrivilegeError("Doris 查询账号未返回有效的授权信息")
        tokens: set[str] = set()
        for row in rows:
            privilege_values = [
                value
                for key, value in row.items()
                if key.casefold().endswith("privs") or "grant" in key.casefold()
            ]
            for value in privilege_values:
                if value is None:
                    continue
                tokens.update(
                    match.group(0).casefold()
                    for match in _PRIVILEGE_PATTERN.finditer(str(value))
                )
        forbidden = sorted(tokens - _ALLOWED_READONLY_PRIVILEGES)
        if forbidden:
            raise DorisReadonlyPrivilegeError(
                "Doris 查询账号包含禁止的非只读权限: " + ", ".join(forbidden)
            )
        if "select_priv" not in tokens and "read_only" not in tokens:
            raise DorisReadonlyPrivilegeError("Doris 查询账号缺少 SELECT_PRIV 只读权限")
        roles: set[str] = set()
        for row in rows:
            for key, value in row.items():
                if key.casefold() != "roles" or value is None:
                    continue
                roles.update(
                    role.strip().strip("'\"")
                    for role in re.split(r"[,;]", str(value))
                    if role.strip()
                )
        if roles != {expected_role}:
            raise DorisReadonlyPrivilegeError(
                "Doris 查询账号必须精确绑定到预期的唯一角色"
            )

    @staticmethod
    def _bounded_sql(sql: str, max_rows: int) -> str:
        """使用外层查询限制数据库实际返回的最大行数"""
        return (
            f"SELECT * FROM ({sql}) AS `_dataagent_readonly_query` LIMIT {max_rows + 1}"
        )

    @staticmethod
    def _literal_sql(sql: str):
        """构造不把 SQL 字符串内冒号解释为绑定参数的语句"""
        return text(sql.replace(":", r"\:"))

    async def explain(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> tuple[str, ...]:
        """在实际读取数据前编译受限查询计划"""
        async with self._connection_provider.connection() as connection:
            try:
                await self._apply_session_limits(connection, limits)
                result = await connection.execute(self._literal_sql(f"EXPLAIN {sql}"))
                return tuple(
                    " | ".join(str(value) for value in row) for row in result.fetchall()
                )
            except asyncio.CancelledError:
                await connection.invalidate()
                raise

    async def stream(
        self,
        sql: str,
        limits: QueryExecutionLimits,
    ) -> AsyncGenerator[QueryBatch]:
        """设置会话限制并流式返回查询结果分区"""
        async with self._connection_provider.connection() as connection:
            try:
                await self._apply_session_limits(connection, limits)
                result = await connection.stream(
                    self._literal_sql(self._bounded_sql(sql, limits.max_rows)),
                    execution_options={
                        "stream_results": True,
                        "yield_per": limits.batch_size,
                    },
                )
                try:
                    column_names = tuple(map(str, result.keys()))
                    yielded = False
                    async for rows in result.partitions(limits.batch_size):
                        yielded = True
                        yield QueryBatch(
                            column_names=column_names,
                            rows=tuple(tuple(row) for row in rows),
                        )
                    if not yielded:
                        yield QueryBatch(column_names=column_names, rows=())
                finally:
                    close_result = result.close()
                    if inspect.isawaitable(close_result):
                        await close_result
            except asyncio.CancelledError:
                await connection.invalidate()
                raise
