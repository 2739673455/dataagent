"""PostgreSQL 业务数据访问。"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text

from app.shared.clients.business_pg_catalog import BusinessPgCatalog


class SourceBusinessPgRepo(BusinessPgCatalog):
    """PostgreSQL 业务数据存储。"""

    async def get_column_values(
        self,
        table_name: str,
        column_name: str,
        limit: int | None = None,
    ) -> list[Any]:
        """获取字段的去重取值。"""
        table_identifier = self._qualified_table(table_name)
        column_identifier = await self._distinct_column(table_name, column_name)
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
        limit: int,
    ) -> dict[str, list[Any]]:
        """采样最多 limit 行，并提取各字段的非空去重示例。"""
        if not column_names:
            return {}
        self._validate_positive_limit(limit, "limit")
        table_identifier = self._qualified_table(table_name)
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

    async def get_value_sync_upper_bound(
        self,
        table_name: str,
        cursor_column: str,
    ) -> Any | None:
        """读取本次字段取值扫描使用的最大游标值。"""
        table_identifier = self._qualified_table(table_name)
        cursor_identifier = self._quote_identifier(cursor_column)
        result = await self._connection.execute(
            text(
                f"select {cursor_identifier} from {table_identifier} "
                f"where {cursor_identifier} is not null "
                f"order by {cursor_identifier} desc limit 1"
            )
        )
        return result.scalar()

    async def iter_column_value_batches(
        self,
        table_name: str,
        column_name: str,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[Any]]:
        """流式读取字段的全部去重取值，每批最多 batch_size 个。"""
        self._validate_positive_limit(batch_size, "batch_size")
        table_identifier = self._qualified_table(table_name)
        column_identifier = await self._distinct_column(table_name, column_name)
        sql = f"select distinct {column_identifier} from {table_identifier}"
        async with self._connection.stream_scalars(
            text(sql),
            execution_options={"yield_per": batch_size},
        ) as result:
            async for values in result.partitions(batch_size):
                yield list(values)

    async def iter_changed_column_value_batches(
        self,
        table_name: str,
        column_name: str,
        cursor_column: str,
        lower_bound: Any,
        upper_bound: Any,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[Any]]:
        """按包含回看范围的闭区间水位窗口，分批读取字段去重取值。"""
        self._validate_positive_limit(batch_size, "batch_size")
        table_identifier = self._qualified_table(table_name)
        column_identifier = await self._distinct_column(table_name, column_name)
        cursor_identifier = self._quote_identifier(cursor_column)
        sql = (
            f"select distinct {column_identifier} from {table_identifier} "
            f"where {cursor_identifier} >= :lower_bound "
            f"and {cursor_identifier} <= :upper_bound"
        )
        async with self._connection.stream_scalars(
            text(sql),
            {"lower_bound": lower_bound, "upper_bound": upper_bound},
            execution_options={"yield_per": batch_size},
        ) as result:
            async for values in result.partitions(batch_size):
                yield list(values)

    async def _distinct_column(self, table_name: str, column_name: str) -> str:
        quoted = self._quote_identifier(column_name)
        column_types = await self.get_column_types(table_name)
        if column_types.get(column_name) == "json":
            return f"CAST({quoted} AS jsonb)"
        return quoted

    @staticmethod
    def _validate_positive_limit(value: int, name: str) -> None:
        """校验只能作为 SQL 整数字面量写入的分页参数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须为正整数")
