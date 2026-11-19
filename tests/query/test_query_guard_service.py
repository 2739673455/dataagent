import unittest
from unittest.mock import AsyncMock

from app.metadata.contracts import QueryCatalogColumn, QueryCatalogSnapshot
from app.query.execution.guard import QueryGuardService


def make_guard() -> QueryGuardService:
    return QueryGuardService(
        AsyncMock(
            return_value=QueryCatalogSnapshot(
                table_names=("orders", "users"),
                columns=(
                    QueryCatalogColumn("orders", "id", "BIGINT"),
                    QueryCatalogColumn("orders", "user_id", "BIGINT"),
                    QueryCatalogColumn("orders", "amount", "DECIMAL(18, 2)"),
                    QueryCatalogColumn("orders", "created_at", "TIMESTAMP"),
                    QueryCatalogColumn("users", "id", "BIGINT"),
                    QueryCatalogColumn("users", "name", "VARCHAR(100)"),
                ),
            )
        ),
        current_database="analytics",
        schema_name="shop",
    )


class QueryGuardSyntaxTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_discovery_and_static_rejections_skip_metadata(self) -> None:
        for sql, valid in (
            ("SHOW TABLES", False),
            ("SELECT table_name FROM information_schema.tables", True),
            ("DELETE FROM orders", False),
            ("SELECT 1; SELECT 2", False),
            ("SELECT SLEEP(1)", False),
        ):
            with self.subTest(sql=sql):
                catalog_loader = AsyncMock(
                    side_effect=AssertionError("此 SQL 不应读取元数据目录")
                )
                result = await QueryGuardService(
                    catalog_loader, current_database="analytics"
                ).check(sql)
                self.assertEqual(result.valid, valid)
                catalog_loader.assert_not_awaited()

    async def test_allows_role_filtered_catalog_discovery_queries(self) -> None:
        for sql in (
            (
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = current_schema() ORDER BY table_name"
            ),
            (
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'shop' AND table_name = 'orders' "
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
            "SELECT * FROM information_schema.schemata": "catalog_relation_not_allowed",
            "SELECT c.column_name FROM information_schema.columns c JOIN orders o ON c.table_name = 'orders'": "catalog_relation_not_allowed",
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

    async def test_allows_catalog_ctes_subqueries_and_unions(self) -> None:
        for sql in (
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
            "SELECT public.unknown_function(1)": "qualified_function_not_allowed",
            "SELECT * FROM orders FOR UPDATE": "forbidden_operation",
            "SELECT id INTO order_copy FROM orders": "forbidden_operation",
            "SELECT $1 AS value": "forbidden_operation",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_allows_explicitly_approved_readonly_time_function(self) -> None:
        result = await make_guard().check("SELECT NOW() AS generated_at")

        self.assertTrue(result.valid)

    async def test_defers_missing_and_ambiguous_references_to_business_pg(self) -> None:
        for sql in (
            "SELECT missing FROM orders",
            "SELECT id FROM orders o JOIN users u ON o.user_id = u.id",
            "SELECT id FROM absent",
            "SELECT * FROM absent",
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_rejects_other_schemas_and_database_prefixes(self) -> None:
        cases = {
            "SELECT id FROM other.orders": "schema_not_allowed",
            "SELECT id FROM ecommerce.shop.orders": "catalog_not_allowed",
            "SELECT * FROM pg_catalog.pg_class": "schema_not_allowed",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertIn(issue_code, {issue.code for issue in result.issues})
        result = await make_guard().check("SELECT id FROM shop.orders")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            [t.qualified_name for t in result.tables], ["analytics.orders"]
        )

    async def test_leaves_duplicate_outputs_to_executor(self) -> None:
        result = await make_guard().check(
            "SELECT o.id, u.id FROM orders o JOIN users u ON o.user_id = u.id"
        )
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.output_columns, ["id", "id"])

    async def test_preserves_star_for_business_pg_authorization(self) -> None:
        result = await make_guard().check("SELECT * FROM orders")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(result.normalized_sql, 'SELECT * FROM "shop".orders')

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
            AsyncMock(return_value=QueryCatalogSnapshot(table_names=(), columns=())),
            current_database="analytics",
            schema_name="shop",
        )
        result = await guard.check("SELECT id FROM orders WHERE amount > 10")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            result.normalized_sql, 'SELECT id FROM "shop".orders WHERE amount > 10'
        )
        self.assertEqual(
            [column.qualified_name for column in result.columns],
            ["analytics.orders.amount", "analytics.orders.id"],
        )

    async def test_catalog_duplicate_outputs_are_left_to_executor(self) -> None:
        result = await make_guard().check(
            "SELECT table_name AS name, table_type AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
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
                "JOIN users u ON o.user_id IS NOT DISTINCT FROM u.id OR NOT(o.user_id IS NOT DISTINCT FROM u.id)"
            ),
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON CAST(o.user_id AS VARCHAR) = CAST(u.id AS VARCHAR)"
            ),
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_defers_expression_type_compatibility_to_business_pg(self) -> None:
        result = await make_guard().check("SELECT amount = 'not-a-number' FROM orders")

        self.assertTrue(result.valid, result.issues)

    async def test_postgres_cast_literals_and_case_sensitive_identifiers(self) -> None:
        for sql, expected in (
            (
                """SELECT ID::text FROM ORDERS WHERE ID > 1""",
                'SELECT CAST(id AS TEXT) FROM "shop".orders WHERE id > 1',
            ),
            (
                """SELECT "OrderID" FROM "QuotedOrders" """,
                'SELECT "OrderID" FROM "shop"."QuotedOrders"',
            ),
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)
                self.assertEqual(result.normalized_sql, expected)
