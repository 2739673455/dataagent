"""跨业务模块使用的数据资产标识。"""

import hashlib
import json


def asset_resource_key(
    data_source: str,
    database_name: str | None = None,
    table_name: str | None = None,
    column_name: str | None = None,
) -> str:
    """生成层级数据资产的稳定资源键。"""
    canonical = json.dumps(
        [data_source, database_name, table_name, column_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
