import unittest

from app.query.services.guard import QueryGuardService


def make_guard() -> QueryGuardService:
    return QueryGuardService()


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
                result = make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_catalog_scope_and_structure_are_left_to_doris(self):
        for sql in (
            "SHOW TABLES FROM other_database",
            "SELECT * FROM information_schema.schemata",
            "SELECT * FROM information_schema.tables",
            "SELECT c.column_name FROM information_schema.columns c JOIN orders o ON c.table_name = 'orders'",
            "WITH t AS (SELECT * FROM information_schema.tables) SELECT * FROM t",
        ):
            with self.subTest(sql=sql):
                result = make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_rejects_other_show_statements_and_dangerous_catalog_queries(self):
        for sql, message in (
            ("SHOW DATABASES", "目录查询仅允许 SHOW TABLES"),
            ("SELECT SLEEP(1) FROM information_schema.tables", "查询包含禁止的函数"),
            (
                "DELETE FROM information_schema.tables",
                "仅允许执行 SELECT 或 WITH 只读查询语句",
            ),
            ("SHOW TABLES; DELETE FROM orders", "仅允许执行单条 SQL 语句"),
        ):
            with self.subTest(sql=sql):
                result = make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertTrue(any(message in issue for issue in result.issues))

    async def test_accepts_qualified_cte_readonly_query(self) -> None:
        result = make_guard().check(
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

    async def test_rejects_dml_multiple_statements_and_dangerous_function(self) -> None:
        cases = {
            "DELETE FROM orders": "仅允许执行 SELECT 或 WITH 只读查询语句",
            "SELECT 1; SELECT 2": "仅允许执行单条 SQL 语句",
            "SELECT SLEEP(1)": "查询包含禁止的函数",
            "SELECT * FROM orders FOR UPDATE": "查询包含禁止的操作",
            "SELECT /*+ SET_VAR(query_timeout=9999) */ * FROM orders": (
                "查询包含禁止的操作"
            ),
            "SELECT @value := 1": "查询包含禁止的操作",
            "SELECT ? AS value": "查询包含禁止的操作",
            "SELECT :name AS value": "查询包含禁止的操作",
            "SELECT id FROM orders WHERE id = ?": "查询包含禁止的操作",
        }
        for sql, message in cases.items():
            with self.subTest(sql=sql):
                result = make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertTrue(any(message in issue for issue in result.issues))

    async def test_allows_readonly_time_function(self) -> None:
        result = make_guard().check("SELECT NOW() AS generated_at")

        self.assertTrue(result.valid)

    async def test_defers_references_and_join_semantics_to_doris(self):
        for sql in (
            "SELECT missing FROM orders",
            "SELECT id FROM orders o JOIN users u ON o.user_id = u.id",
            "SELECT id FROM absent",
            "SELECT id FROM other.orders",
            "SELECT o.id FROM orders o JOIN users u",
            "SELECT o.id FROM orders o CROSS JOIN users u",
            "SELECT o.id FROM orders o JOIN users u ON o.id = o.id",
            "SELECT o.id, u.id FROM orders o JOIN users u ON o.user_id = u.id",
        ):
            with self.subTest(sql=sql):
                result = make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

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
                result = make_guard().check(sql)
                self.assertTrue(result.valid, result.issues)

    async def test_defers_expression_type_compatibility_to_doris(self) -> None:
        result = make_guard().check("SELECT amount = 'not-a-number' FROM orders")

        self.assertTrue(result.valid, result.issues)


class QueryGuardDorisAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    async def test_star_is_preserved_for_doris(self):
        result = make_guard().check("SELECT * FROM orders WHERE amount > 10")
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(
            result.normalized_sql, "SELECT * FROM orders WHERE amount > 10"
        )


class QueryGuardFunctionTest(unittest.IsolatedAsyncioTestCase):
    async def test_unrecognized_functions_are_left_to_doris(self):
        result = make_guard().check("SELECT custom_function(amount) FROM orders")
        self.assertTrue(result.valid, result.issues)


class QueryGuardNestedStatementTest(unittest.IsolatedAsyncioTestCase):
    async def test_nested_statements_cannot_bypass_readonly_check(self):
        for sql in (
            "WITH x AS (DELETE FROM users RETURNING id) SELECT * FROM x",
            "WITH x AS (UPDATE users SET name = 'x' RETURNING id) SELECT * FROM x",
            "WITH x AS (DROP TABLE users) SELECT 1",
        ):
            with self.subTest(sql=sql):
                result = make_guard().check(sql)
                self.assertFalse(result.valid)
                self.assertTrue(any("禁止的操作" in issue for issue in result.issues))

    async def test_string_replace_is_a_readonly_function(self):
        result = make_guard().check("SELECT REPLACE(name, 'a', 'b') FROM users")
        self.assertTrue(result.valid, result.issues)
