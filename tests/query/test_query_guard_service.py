import unittest

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.query.services.guard import QueryGuardService, QueryRejectedError


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

    async def list_table_infos(self) -> list[TableInfo]:
        return self.tables

    async def list_column_infos(self) -> list[ColumnInfo]:
        return self.columns


class StaticPolicyProvider:
    def __init__(self, policy: AssetAccessPolicy) -> None:
        self.policy = policy

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy:
        if self.policy.user_id != user_id:
            raise AssertionError("unexpected user")
        return self.policy


def make_guard(policy: AssetAccessPolicy | None = None) -> QueryGuardService:
    return QueryGuardService(
        FakeCatalogRepo(),
        data_source="doris",
        current_database="analytics",
        max_cell_bytes=1024 * 1024,
        policy_provider=StaticPolicyProvider(policy) if policy else None,
    )


class QueryGuardSyntaxTest(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_qualified_cte_readonly_query(self) -> None:
        result = await make_guard().check(
            7,
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
            "SELECT SLEEP(1)": "forbidden_function",
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
                result = await make_guard().check(7, sql)
                self.assertFalse(result.valid)
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_allows_explicitly_approved_readonly_time_function(self) -> None:
        result = await make_guard().check(7, "SELECT NOW() AS generated_at")

        self.assertTrue(result.valid)

    async def test_rejects_unknown_and_ambiguous_references(self) -> None:
        cases = {
            "SELECT missing FROM orders": "unknown_column",
            "SELECT id FROM orders o JOIN users u ON o.user_id = u.id": (
                "ambiguous_column"
            ),
            "SELECT id FROM absent": "unknown_table",
            "SELECT id FROM other.orders": "unknown_database",
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(7, sql)
                self.assertIn(issue_code, {issue.code for issue in result.issues})

    async def test_rejects_invalid_join_and_type_conflicts(self) -> None:
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
                "JOIN users u ON (o.user_id = u.id) IS NOT NULL"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.id + u.id > 0"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON COALESCE(o.id, 0) * 0 = COALESCE(u.id, 0) * 0"
            ): "invalid_join_condition",
            (
                "SELECT o.id AS order_id, u.id AS user_id FROM orders o "
                "JOIN users u ON o.user_id <=> u.id OR NOT(o.user_id <=> u.id)"
            ): "invalid_join_condition",
            "SELECT amount = 'not-a-number' FROM orders": "incompatible_types",
            "SELECT o.id, u.id FROM orders o JOIN users u ON o.user_id = u.id": (
                "duplicate_output_column"
            ),
        }
        for sql, issue_code in cases.items():
            with self.subTest(sql=sql):
                result = await make_guard().check(7, sql)
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
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(7, sql)
                self.assertTrue(result.valid, result.issues)

    async def test_rejects_static_oversized_string_expansion(self) -> None:
        for sql in (
            "SELECT REPEAT('x', 500000000)",
            "SELECT RPAD('x', 500000000, 'y')",
            "SELECT SPACE(500000000)",
        ):
            with self.subTest(sql=sql):
                result = await make_guard().check(7, sql)
                self.assertFalse(result.valid)
                self.assertIn(
                    "value_expansion_too_large",
                    {issue.code for issue in result.issues},
                )

    async def test_require_safe_never_returns_rejected_sql(self) -> None:
        with self.assertRaises(QueryRejectedError):
            await make_guard().require_safe(7, "UPDATE orders SET amount = 0")


class QueryGuardAuthorizationTest(unittest.IsolatedAsyncioTestCase):
    async def test_column_grant_allows_explicit_column(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                        column_name="id",
                    )
                }
            ),
        )
        result = await make_guard(policy).check(7, "SELECT id FROM orders")
        self.assertTrue(result.valid)

    async def test_column_grant_cannot_authorize_star(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                        column_name="id",
                    )
                }
            ),
        )
        result = await make_guard(policy).check(7, "SELECT * FROM orders")
        self.assertEqual(
            {issue.code for issue in result.issues},
            {"column_access_denied"},
        )

    async def test_explicit_filter_column_is_also_authorized(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                        column_name="id",
                    )
                }
            ),
        )
        result = await make_guard(policy).check(
            7,
            "SELECT id FROM orders WHERE amount > 10",
        )
        self.assertEqual(
            {issue.column for issue in result.issues},
            {"amount"},
        )

    async def test_denied_and_missing_resources_are_indistinguishable(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                        column_name="id",
                    )
                }
            ),
        )
        denied_table = await make_guard(policy).check(7, "SELECT 1 FROM users")
        missing_table = await make_guard(policy).check(7, "SELECT 1 FROM secrets")
        denied_column = await make_guard(policy).check(
            7,
            "SELECT amount FROM orders",
        )
        missing_column = await make_guard(policy).check(
            7,
            "SELECT secret_amount FROM orders",
        )

        self.assertEqual(
            [issue.code for issue in denied_table.issues],
            [issue.code for issue in missing_table.issues],
        )
        self.assertEqual(
            [issue.code for issue in denied_column.issues],
            [issue.code for issue in missing_column.issues],
        )

    async def test_table_grant_allows_star(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                    )
                }
            ),
        )
        result = await make_guard(policy).check(7, "SELECT * FROM orders")
        self.assertTrue(result.valid)

    async def test_qualified_star_does_not_require_other_joined_table_grant(
        self,
    ) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                    ),
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="users",
                        column_name="id",
                    ),
                }
            ),
        )
        result = await make_guard(policy).check(
            7,
            """
            SELECT o.*, u.id AS user_pk
            FROM orders o
            JOIN users u ON o.user_id = u.id
            """,
        )
        self.assertTrue(result.valid)
