import unittest
from unittest.mock import AsyncMock, MagicMock

from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.repositories.postgres import MetaPGRepo
from app.query.services.guard import QueryGuardService


def make_column(table: str, name: str, data_type: str) -> ColumnInfo:
    return ColumnInfo(
        t_name=table,
        name=name,
        type=data_type,
        description="",
        examples=[],
        alias=[],
        index_values=False,
    )


class FakeCatalogRepo:
    def __init__(self) -> None:
        self.tables = [
            TableInfo(
                name="orders",
                role="fact",
                primary_key_columns=["id"],
                description="",
            ),
            TableInfo(
                name="users",
                role="dim",
                primary_key_columns=["id"],
                description="",
            ),
        ]
        self.columns = [
            make_column("orders", "id", "BIGINT"),
            make_column("orders", "user_id", "BIGINT"),
            make_column("orders", "amount", "DECIMAL(18, 2)"),
            make_column("orders", "created_at", "DATETIME"),
            make_column("users", "id", "BIGINT"),
            make_column("users", "name", "VARCHAR(100)"),
        ]


def make_guard() -> QueryGuardService:
    catalog = FakeCatalogRepo()
    return QueryGuardService(
        MagicMock(
            spec=MetaPGRepo,
            list_table_infos=AsyncMock(return_value=catalog.tables),
            list_column_infos=AsyncMock(return_value=catalog.columns),
        ),
        current_database="analytics",
    )


class QueryGuardSyntaxTest(unittest.IsolatedAsyncioTestCase):
    async def test_allows_role_filtered_catalog_discovery_queries(self) -> None:
        for sql in (
            "SHOW TABLES",
            "SHOW FULL TABLES FROM analytics LIKE 'order%'",
            (
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name"
            ),
            (
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'analytics' AND table_name = 'orders' "
                "ORDER BY ordinal_position"
            ),
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)
                self.assertEqual(result.query_kind, "catalog")
                self.assertEqual(result.tables, [])
                self.assertEqual(result.columns, [])

    async def test_rejects_catalog_queries_outside_discovery_allowlist(self) -> None:
        cases = {
            "SHOW DATABASES": "catalog_statement_not_allowed",
            "SELECT * FROM external.information_schema.tables": "catalog_not_allowed",
            "DELETE FROM information_schema.tables": "readonly_query_required",
            (
                "WITH t AS (SELECT * FROM external.information_schema.tables) SELECT * FROM t"
            ): "catalog_not_allowed",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertEqual(result.query_kind, "catalog")
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_allows_complex_catalog_queries_and_other_system_tables(self) -> None:
        for sql in (
            "SELECT * FROM information_schema.schemata",
            "SELECT c.column_name FROM information_schema.columns c JOIN orders o ON c.table_name = 'orders'",
            "WITH t AS (SELECT table_name FROM information_schema.tables) SELECT * FROM t",
            "SELECT * FROM (SELECT table_name FROM information_schema.tables) t",
            "SELECT table_name FROM information_schema.tables UNION ALL SELECT column_name FROM information_schema.columns",
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)
                self.assertEqual(result.query_kind, "catalog")
                self.assertIsNotNone(result.normalized_sql)

    async def test_rejects_write_statements_inside_ctes(self) -> None:
        for statement in (
            "DROP TABLE orders",
            "DELETE FROM orders RETURNING id",
            "UPDATE orders SET amount = 1 RETURNING id",
            "INSERT INTO orders (id) VALUES (1)",
        ):
            with self.subTest(statement=statement):
                result = await make_guard().check(
                    f"WITH changed AS ({statement}) SELECT 1 AS value"
                )
                self.assertFalse(result.valid)
                self.assertIn(
                    "forbidden_operation", [issue.code for issue in result.issues]
                )
                self.assertIsNone(result.normalized_sql)

    async def test_allows_replace_string_function(self) -> None:
        result = await make_guard().check("SELECT REPLACE('abc', 'a', 'b') AS value")
        self.assertTrue(result.valid, result.issues)

    async def test_accepts_qualified_cte_readonly_query(self) -> None:
        result = await make_guard().check(
            """
            WITH order_totals AS (
                SELECT user_id, SUM(amount) AS total
                FROM orders
                GROUP BY user_id
            )
            SELECT u.name, o.total
            FROM order_totals o
            JOIN users u ON o.user_id = u.id
            """,
        )

        self.assertTrue(result.valid)
        self.assertEqual(
            [column.qualified_name for column in result.columns],
            [
                "analytics.orders.amount",
                "analytics.orders.user_id",
                "analytics.users.id",
                "analytics.users.name",
            ],
        )
        self.assertEqual(result.output_columns, ["name", "total"])

    async def test_rejects_dml_multiple_statements_and_dangerous_function(self) -> None:
        cases = {
            "DELETE FROM orders": "readonly_query_required",
            "SELECT 1; SELECT 2": "multiple_statements",
            "SELECT SLEEP(1)": "unapproved_function",
            "SELECT HTTP_GET('http://169.254.169.254/')": "unapproved_function",
            "SELECT LAST_INSERT_ID(123)": "unapproved_function",
            "SELECT * FROM S3('uri'='http://example.invalid/data')": (
                "unapproved_function"
            ),
            "SELECT * FROM orders FOR UPDATE": "forbidden_operation",
            "SELECT /*+ SET_VAR(query_timeout=9999) */ * FROM orders": (
                "forbidden_operation"
            ),
            "SELECT @value := 1": "forbidden_operation",
            "SELECT ? AS value": "forbidden_operation",
            "SELECT :name AS value": "forbidden_operation",
            "SELECT id FROM orders WHERE id = ?": "forbidden_operation",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_allows_explicitly_approved_readonly_time_function(self) -> None:
        result = await make_guard().check("SELECT NOW() AS generated_at")

        self.assertTrue(result.valid)

    async def test_defers_missing_and_ambiguous_references_to_doris(self) -> None:
        for sql in (
            "SELECT missing FROM orders",
            "SELECT id FROM orders o JOIN users u ON o.user_id = u.id",
            "SELECT id FROM absent",
            "SELECT * FROM absent",
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_database_scope_is_checked_by_doris(self) -> None:
        for sql in (
            "SELECT id FROM other.orders",
            "SHOW TABLES FROM other_database",
            "SELECT * FROM information_schema.tables",
            "SELECT * FROM information_schema.columns WHERE table_schema = 'other'",
            "SELECT * FROM information_schema.tables WHERE table_schema = DATABASE() OR 1 = 1",
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)
        result = await make_guard().check("SELECT new_column FROM other.orders")
        self.assertEqual([t.qualified_name for t in result.tables], ["other.orders"])
        self.assertEqual(
            [c.qualified_name for c in result.columns], ["other.orders.new_column"]
        )

    async def test_leaves_duplicate_outputs_to_executor(self) -> None:
        result = await make_guard().check(
            "SELECT o.id, u.id FROM orders o JOIN users u ON o.user_id = u.id"
        )
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.output_columns, ["id", "id"])

    async def test_preserves_star_for_doris_authorization(self) -> None:
        result = await make_guard().check("SELECT * FROM orders")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.normalized_sql, "SELECT * FROM orders")

    async def test_records_missing_catalog_references(self) -> None:
        result = await make_guard().check("SELECT new_column FROM new_table")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            [t.qualified_name for t in result.tables], ["analytics.new_table"]
        )
        self.assertEqual(
            [c.qualified_name for c in result.columns],
            ["analytics.new_table.new_column"],
        )

    async def test_empty_catalog_does_not_block_query(self) -> None:
        guard = QueryGuardService(
            MagicMock(
                spec=MetaPGRepo,
                list_table_infos=AsyncMock(return_value=[]),
                list_column_infos=AsyncMock(return_value=[]),
            ),
            current_database="analytics",
        )
        result = await guard.check("SELECT id FROM orders WHERE amount > 10")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            result.normalized_sql, "SELECT id FROM orders WHERE amount > 10"
        )
        self.assertEqual(
            [column.qualified_name for column in result.columns],
            ["analytics.orders.amount", "analytics.orders.id"],
        )

    async def test_catalog_duplicate_outputs_are_left_to_executor(self) -> None:
        result = await make_guard().check(
            "SELECT table_name AS name, table_type AS name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        self.assertTrue(result.valid, result.issues)

    async def test_rejects_invalid_join(self) -> None:
        cases = {
            "SELECT o.id FROM orders o JOIN users u": "join_condition_required",
            "SELECT o.id FROM orders o CROSS JOIN users u": "cross_join_forbidden",
            "SELECT o.id FROM orders o JOIN users u ON o.id = o.id": (
                "invalid_join_condition"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.id > 0 AND u.id > 0"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.user_id = u.id OR (o.id > 0 AND u.id > 0)"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON CASE WHEN o.id > 0 THEN u.id > 0 ELSE TRUE END"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.id = o.id + (u.id - u.id)"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.id + u.id > 0"
            ): "invalid_join_condition",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_accepts_join_using_and_cross_source_predicates(self) -> None:
        for sql in (
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u USING (id)"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.user_id < u.id"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.user_id = u.id AND o.id > 0 AND u.id > 0"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON (o.user_id = u.id) IS NOT NULL"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON COALESCE(o.id, 0) * 0 = COALESCE(u.id, 0) * 0"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.user_id <=> u.id OR NOT(o.user_id <=> u.id)"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON CAST(o.user_id AS VARCHAR) = CAST(u.id AS VARCHAR)"
            ),
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_defers_expression_type_compatibility_to_doris(self) -> None:
        result = await make_guard().check("SELECT amount = 'not-a-number' FROM orders")

        self.assertTrue(result.valid, result.issues)
