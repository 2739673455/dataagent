"""解析 Doris 4.x SHOW GRANTS，生成业务数据库的有效权限快照。"""

import re
from collections.abc import Mapping

from app.identity import errors as auth_error
from app.identity.models.doris import DorisAuthorizationSnapshot, DorisSelectGrant


def parse_authorization(
    row: Mapping[str, object],
    *,
    role_name: str,
    query_user: str,
    data_source: str,
    catalog: str,
    database: str,
) -> DorisAuthorizationSnapshot:
    """解析有效权限，身份不匹配或授权格式无效时抛出异常。"""
    if _text(row, "UserIdentity") != f"'{query_user}'@'%'":
        raise auth_error.InvalidDorisPermissionError(
            detail="Doris 查询用户身份与平台配置不一致"
        )
    roles = {item.strip() for item in _text(row, "Roles").split(",") if item.strip()}
    if roles != {role_name}:
        raise auth_error.InvalidDorisPermissionError(
            detail="Doris 查询账号必须只绑定平台配置的业务角色"
        )
    global_privileges = _privileges(_text(row, "GlobalPrivs"))
    if "admin_priv" in global_privileges or "node_priv" in global_privileges:
        raise auth_error.InvalidDorisPermissionError(
            detail="业务查询账号不能具有 Doris 管理权限"
        )
    grants: set[DorisSelectGrant] = set()
    if "select_priv" in global_privileges:
        grants.add(DorisSelectGrant(role_name, data_source, database))
    for field, arity in (
        ("CatalogPrivs", 1),
        ("DatabasePrivs", 2),
        ("TablePrivs", 3),
        ("ColPrivs", 3),
    ):
        raw = _text(row, field)
        if raw not in {"", "NULL"}:
            for entry in raw.split("; "):
                target, separator, value = entry.partition(": ")
                parts = target.split(".")
                if (
                    not separator
                    or len(parts) != arity
                    or any(not part for part in parts)
                ):
                    raise auth_error.InvalidDorisPermissionError(
                        detail=f"无法解析 Doris {field} 的授权目标"
                    )
                if field == "ColPrivs":
                    match = re.fullmatch(
                        r"Select_priv\[([^\[\]]+)\]", value, re.IGNORECASE
                    )
                    if match is None:
                        raise auth_error.InvalidDorisPermissionError(
                            detail="无法解析 Doris 列权限"
                        )
                    values = tuple(
                        sorted({item.strip() for item in match[1].split(",")})
                    )
                    if any(
                        not item or re.search(r"[\s;:\[\]]", item) for item in values
                    ):
                        raise auth_error.InvalidDorisPermissionError(
                            detail="无法解析 Doris 列权限字段名"
                        )
                else:
                    values = _privileges(value)
                if parts[0] != catalog:
                    continue
                if arity >= 2 and parts[1] != database:
                    continue
                if field == "ColPrivs":
                    grants.update(
                        DorisSelectGrant(
                            role_name, data_source, database, parts[2], col
                        )
                        for col in values
                    )
                elif "select_priv" in values:
                    grants.add(
                        DorisSelectGrant(
                            role_name,
                            data_source,
                            database,
                            parts[2] if arity == 3 else None,
                        )
                    )
    return DorisAuthorizationSnapshot(
        grants=tuple(
            sorted(
                grants,
                key=lambda item: (
                    item.database_name,
                    item.table_name or "",
                    item.column_name or "",
                ),
            )
        ),
    )


def _text(row: Mapping[str, object], key: str) -> str:
    """读取必需的 SHOW 结果字段，将 SQL NULL 视为空值。"""
    if key in row and row[key] is None:
        return ""
    if key not in row or not isinstance(row[key], str):
        raise auth_error.InvalidDorisPermissionError(
            detail=f"Doris 授权结果缺少有效的 {key} 字段"
        )
    return str(row[key]).strip()


def _privileges(value: str) -> tuple[str, ...]:
    """规范化权限名称并排序，拒绝无法识别的名称格式。"""
    if value in {"", "NULL"}:
        return ()
    tokens = tuple(sorted(item.strip().lower() for item in value.split(",")))
    if any(not re.fullmatch(r"[a-z_]+_priv", token) for token in tokens):
        raise auth_error.InvalidDorisPermissionError(detail="无法解析 Doris 权限标识")
    return tokens
