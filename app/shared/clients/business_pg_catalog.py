"""业务 PostgreSQL 主键、字段和注释目录。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


class BusinessPgCatalog:
    """读取指定 schema 的数据库对象目录。"""

    def __init__(self, connection: AsyncConnection, schema_name: str) -> None:
        """绑定当前数据库及明确配置的业务 schema。"""
        self._connection = connection
        self._schema_name = schema_name

    async def list_tables(self) -> list[str]:
        """列出业务 schema 中可授权的普通表和分区父表。"""
        result = await self._connection.execute(
            text(
                """
                SELECT c.relname
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relkind IN ('r', 'p')
                  AND NOT c.relispartition
                ORDER BY c.relname
                """
            ),
            {"schema_name": self._schema_name},
        )
        return list(result.scalars())

    async def table_exists(self, table_name: str) -> bool:
        """判断配置范围内是否存在指定表。"""
        result = await self._connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema_name AND c.relname = :table_name
                      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
                )
                """
            ),
            {"schema_name": self._schema_name, "table_name": table_name},
        )
        return bool(result.scalar_one())

    async def get_primary_key_columns(self, table_name: str) -> list[str]:
        """按 PostgreSQL 主键定义顺序读取字段，不包含 INCLUDE 字段。"""
        result = await self._connection.execute(
            text(
                """
                SELECT a.attname
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_constraint AS p ON p.conrelid = c.oid
                CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_catalog.pg_attribute AS a
                    ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                  AND c.relkind IN ('r', 'p') AND NOT c.relispartition AND p.contype = 'p'
                ORDER BY k.ord
                """
            ),
            {"schema_name": self._schema_name, "table_name": table_name},
        )
        return list(result.scalars())

    async def get_table_comment(self, table_name: str) -> str | None:
        """读取数据库内的表注释。"""
        result = await self._connection.execute(
            text(
                """
                SELECT pg_catalog.obj_description(c.oid, 'pg_class')
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                  AND c.relkind IN ('r', 'p') AND NOT c.relispartition
                """
            ),
            {"schema_name": self._schema_name, "table_name": table_name},
        )
        return result.scalar_one_or_none()

    async def get_column_definitions(
        self, table_name: str
    ) -> list[tuple[str, str, str | None]]:
        """按定义顺序获取字段、PostgreSQL 类型与列注释。"""
        result = await self._connection.execute(
            text(
                """
                SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
                       pg_catalog.col_description(c.oid, a.attnum)
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                  AND c.relkind IN ('r', 'p') AND NOT c.relispartition
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """
            ),
            {"schema_name": self._schema_name, "table_name": table_name},
        )
        return [(name, data_type, comment) for name, data_type, comment in result]

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """获取表的字段类型，保留长度、精度等原生定义。"""
        return {
            name: data_type
            for name, data_type, _ in await self.get_column_definitions(table_name)
        }

    def _qualified_table(self, table_name: str) -> str:
        """数据读取显式限定业务 schema，不依赖 search_path。"""
        return f"{self._quote_identifier(self._schema_name)}.{self._quote_identifier(table_name)}"

    def _quote_identifier(self, identifier: str) -> str:
        """使用当前数据库方言安全引用标识符。"""
        if not identifier or "\x00" in identifier:
            raise ValueError(f"数据库标识符无效: {identifier}")
        return self._connection.dialect.identifier_preparer.quote_identifier(identifier)
