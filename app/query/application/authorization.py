"""查询经验资产的公开权限检查。"""

from app.identity.contracts import AssetAccessPolicy, AssetIdentity
from app.query.contracts import QueryAssetSnapshot


def query_assets_are_allowed(
    assets: list[QueryAssetSnapshot],
    policy: AssetAccessPolicy,
    data_source: str,
    database_name: str,
) -> bool:
    """按实际引用字段检查权限；无字段引用的表要求整表可读。"""
    tables = {asset.table for asset in assets if asset.kind == "table"}
    columns_by_table: dict[str, set[str]] = {}
    for asset in assets:
        if asset.kind == "column" and asset.column is not None:
            tables.add(asset.table)
            columns_by_table.setdefault(asset.table, set()).add(asset.column)
    return all(
        all(
            policy.allows(AssetIdentity(data_source, database_name, table, column))
            for column in columns_by_table[table]
        )
        if columns_by_table.get(table)
        else policy.allows(AssetIdentity(data_source, database_name, table))
        for table in tables
    )
