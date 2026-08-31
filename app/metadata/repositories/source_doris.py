"""Doris 业务数据访问。"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


class SourceDorisRepo:
    """Doris 业务数据存储。"""

    def __init__(self, connection: AsyncConnection) -> None:
        """初始化 Doris 业务数据存储。"""
        self._connection = connection

    def _quote_identifier(self, identifier: str) -> str:
        """使用当前数据库方言安全引用标识符。"""
        if not identifier or "\x00" in identifier:
            raise ValueError(f"数据库标识符无效: {identifier}")
        return self._connection.dialect.identifier_preparer.quote_identifier(identifier)

    @staticmethod
    def _validate_positive_limit(value: int, name: str) -> None:
        """校验只能作为 SQL 整数字面量写入的分页参数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须为正整数")

    async def list_tables(self) -> list[str]:
        """查询当前 Doris 数据库中全部物理表名。"""
        result = await self._connection.execute(
            text(
                """
                select table_name
                from information_schema.tables
                where table_schema = database()
                  and table_type in ('BASE TABLE', 'VIEW')
                order by table_name
                """
            )
        )
        return list(result.scalars().fetchall())

    async def table_exists(self, table_name: str) -> bool:
        """判断当前 Doris 数据库中是否存在指定表。"""
        result = await self._connection.execute(
            text(
                """
                select exists(
                    select 1
                    from information_schema.tables
                    where table_schema = database()
                      and table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar())

    async def get_primary_key_columns(self, table_name: str) -> list[str]:
        """按定义顺序获取 Doris UNIQUE KEY 字段作为逻辑主键。"""
        result = await self._connection.execute(
            text(
                """
                select column_name
                from information_schema.columns
                where table_schema = database()
                  and table_name = :table_name
                  and column_key = 'UNI'
                order by ordinal_position
                """
            ),
            {"table_name": table_name},
        )
        return list(result.scalars().fetchall())

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """获取表的字段类型。"""
        result = await self._connection.execute(
            text(
                """
                select column_name, column_type
                from information_schema.columns
                where table_schema = database()
                  and table_name = :table_name
                order by ordinal_position
                """
            ),
            {"table_name": table_name},
        )
        return {row[0]: row[1] for row in result.fetchall()}

    async def get_column_values(
        self,
        table_name: str,
        column_name: str,
        limit: int | None = None,
    ) -> list[Any]:
        """获取字段的去重取值。"""
        table_identifier = self._quote_identifier(table_name)
        column_identifier = self._quote_identifier(column_name)
        sql = f"select distinct {column_identifier} from {table_identifier}"
        if limit is not None:
            self._validate_positive_limit(limit, "limit")
            sql = f"{sql} limit {limit}"
        result = await self._connection.execute(text(sql))
        return list(result.scalars().fetchall())

    async def get_table_columns_sample_values(
        self,
        table_name: str,
        column_names: list[str],
        limit: int = 5,
    ) -> dict[str, list[Any]]:
        """批量获取指定表中多个字段的样例取值。"""
        if not column_names:
            return {}
        self._validate_positive_limit(limit, "limit")
        table_identifier = self._quote_identifier(table_name)
        quoted_cols = [self._quote_identifier(c) for c in column_names]
        sql = f"select {', '.join(quoted_cols)} from {table_identifier} limit {limit}"
        result = await self._connection.execute(text(sql))
        rows = result.fetchall()
        column_values: dict[str, list[Any]] = {c: [] for c in column_names}
        for row in rows:
            for index, c in enumerate(column_names):
                val = row[index]
                if val is not None and val not in column_values[c]:
                    column_values[c].append(val)
        return column_values

    async def iter_column_value_batches(
        self,
        table_name: str,
        column_name: str,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[Any]]:
        """流式分批读取字段的去重取值。"""
        self._validate_positive_limit(batch_size, "batch_size")
        table_identifier = self._quote_identifier(table_name)
        column_identifier = self._quote_identifier(column_name)
        sql = f"select distinct {column_identifier} from {table_identifier}"
        result = await self._connection.stream_scalars(
            text(sql),
            execution_options={"yield_per": batch_size},
        )
        async for values in result.partitions(batch_size):
            yield list(values)

    async def get_value_sync_upper_bound(
        self,
        table_name: str,
        cursor_column: str,
    ) -> Any | None:
        """读取字段取值增量同步的固定游标上界。"""
        table_identifier = self._quote_identifier(table_name)
        cursor_identifier = self._quote_identifier(cursor_column)
        result = await self._connection.execute(
            text(f"select max({cursor_identifier}) from {table_identifier}")
        )
        return result.scalar()

    async def iter_changed_column_value_batches(
        self,
        table_name: str,
        column_name: str,
        cursor_column: str,
        lower_bound: Any,
        upper_bound: Any,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[Any]]:
        """按闭区间水位窗口分批读取字段去重取值。"""
        self._validate_positive_limit(batch_size, "batch_size")
        table_identifier = self._quote_identifier(table_name)
        column_identifier = self._quote_identifier(column_name)
        cursor_identifier = self._quote_identifier(cursor_column)
        sql = (
            f"select distinct {column_identifier} from {table_identifier} "
            f"where {cursor_identifier} >= :lower_bound "
            f"and {cursor_identifier} <= :upper_bound"
        )
        result = await self._connection.stream_scalars(
            text(sql),
            {"lower_bound": lower_bound, "upper_bound": upper_bound},
            execution_options={"yield_per": batch_size},
        )
        async for values in result.partitions(batch_size):
            yield list(values)
