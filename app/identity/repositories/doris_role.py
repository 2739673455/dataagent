"""读取 Doris 查询账号的实际权限。"""

from collections.abc import Mapping
from typing import Any

from loguru import logger

from app.identity import errors as auth_error
from app.identity.models.authorization import AssetIdentity
from app.shared.clients.doris_client_manager import DorisClientManager


class DorisRoleRepository:
    """使用管理连接读取查询账号权限。"""

    def __init__(self, provider: DorisClientManager) -> None:
        """绑定 Doris 管理连接提供器。"""
        self._provider = provider

    async def read_authorization(
        self,
        *,
        role_name: str,
        query_user: str,
        data_source: str,
        catalog: str,
        database: str,
    ) -> frozenset[AssetIdentity]:
        """读取查询账号授权，提取指定业务库的 SELECT 资产范围。"""
        async with self._provider.connection() as connection:
            await connection.exec_driver_sql("SET show_user_default_role = false")
            result = await connection.exec_driver_sql(
                "SHOW GRANTS FOR %s@%s", (query_user, "%")
            )
            rows = result.mappings().all()
            if len(rows) != 1:
                raise ValueError("Doris 查询账号授权结果必须唯一")
            return _parse_authorization(
                rows[0],
                role_name=role_name,
                data_source=data_source,
                catalog=catalog,
                database=database,
            )


def _parse_authorization(
    row: Mapping[Any, object],
    *,
    role_name: str,
    data_source: str,
    catalog: str,
    database: str,
) -> frozenset[AssetIdentity]:
    """检查角色绑定，提取指定库的 SELECT 授权；解析失败的条目记录日志后跳过。"""
    roles = {item.strip() for item in _text(row, "Roles").split(",") if item.strip()}
    if role_name not in roles:
        raise auth_error.InvalidDorisPermissionError(
            detail=f"Doris 查询账号未绑定配置角色: {role_name}"
        )
    global_privileges = {item.strip() for item in _text(row, "GlobalPrivs").split(",")}
    grants: set[AssetIdentity] = set()
    if "Select_priv" in global_privileges:
        grants.add(AssetIdentity(data_source, database))
    for field, arity in (
        ("CatalogPrivs", 1),
        ("DatabasePrivs", 2),
        ("TablePrivs", 3),
        ("ColPrivs", 3),
    ):
        raw = _text(row, field)
        if raw in {"", "NULL"}:
            continue
        for entry in raw.split("; "):
            try:
                target, value = entry.split(": ", 1)
                parts = target.split(".")
                if field == "ColPrivs":
                    value = value.split("[", 1)[1].removesuffix("]")
                values = {item.strip() for item in value.split(",") if item.strip()}
                if parts[0] != catalog or (arity >= 2 and parts[1] != database):
                    continue
                columns: set[str] | tuple[None, ...]
                if field == "ColPrivs":
                    columns = values
                else:
                    columns = (None,) if "Select_priv" in values else ()
                grants.update(
                    AssetIdentity(
                        data_source,
                        database,
                        parts[2] if arity == 3 else None,
                        column,
                    )
                    for column in columns
                )
            except (ValueError, IndexError) as exc:
                logger.warning(
                    "跳过无法解析的 Doris 授权: role={} field={} entry={!r} error={}",
                    role_name,
                    field,
                    entry,
                    exc,
                )
    return frozenset(grants)


def _text(row: Mapping[Any, object], key: str) -> str:
    """读取 SHOW GRANTS 文本字段，将 SQL NULL 转为空字符串。"""
    if key not in row:
        raise auth_error.InvalidDorisPermissionError(
            detail=f"Doris 授权结果缺少有效的 {key} 字段"
        )
    value = row[key]
    if value is None:
        return ""
    if not isinstance(value, str):
        raise auth_error.InvalidDorisPermissionError(
            detail=f"Doris 授权结果缺少有效的 {key} 字段"
        )
    return value.strip()
