import unittest

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter


def make_column(
    table_name: str,
    column_name: str,
    *,
    reference: tuple[str, str] | None = None,
) -> ColumnInfo:
    return ColumnInfo(
        t_name=table_name,
        name=column_name,
        type="BIGINT",
        description=column_name,
        examples=[],
        alias=[],
        index_values=False,
        reference_t_name=reference[0] if reference else None,
        reference_c_name=reference[1] if reference else None,
        meta_version=1,
        index_version=1,
    )


class MetadataAuthorizationFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        policy = AssetAccessPolicy(
            user_id=7,
            grants=frozenset(
                {
                    AssetIdentity(
                        data_source="doris",
                        database_name="analytics",
                        table_name="orders",
                        column_name="customer_id",
                    )
                }
            ),
        )
        self.filter = MetadataAuthorizationFilter(policy, "doris", "analytics")
        self.columns = [
            make_column(
                "orders",
                "customer_id",
                reference=("customers", "id"),
            ),
            make_column("orders", "secret_id"),
            make_column("customers", "id"),
        ]

    def test_redacts_primary_keys_and_foreign_key_targets(self) -> None:
        allowed = self.filter.allowed_column_keys(self.columns)
        tables = self.filter.filter_tables(
            [
                TableInfo(
                    name="orders",
                    role="fact",
                    primary_key_columns=["secret_id"],
                    description="orders",
                ),
                TableInfo(
                    name="customers",
                    role="dim",
                    primary_key_columns=["id"],
                    description="customers",
                ),
            ],
            allowed,
        )
        columns = self.filter.filter_columns(self.columns, allowed)

        self.assertEqual([table.name for table in tables], ["orders"])
        self.assertEqual(tables[0].primary_key_columns, [])
        self.assertEqual([column.name for column in columns], ["customer_id"])
        self.assertIsNone(columns[0].reference_t_name)
        self.assertIsNone(columns[0].reference_c_name)

    def test_database_grant_keeps_all_database_metadata(self) -> None:
        authorization_filter = MetadataAuthorizationFilter(
            AssetAccessPolicy(
                user_id=1,
                grants=frozenset({AssetIdentity("doris", "analytics")}),
            ),
            "doris",
            "analytics",
        )
        allowed = authorization_filter.allowed_column_keys(self.columns)
        filtered = authorization_filter.filter_columns(self.columns, allowed)

        self.assertEqual(filtered[0].reference_t_name, "customers")


if __name__ == "__main__":
    unittest.main()
