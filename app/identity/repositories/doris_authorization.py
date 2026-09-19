"""解析 Doris 4.x SHOW GRANTS，生成业务数据库的有效权限快照。"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from app.identity import errors as auth_error
from app.identity.models.doris import DorisAuthorizationSnapshot, DorisSelectGrant


def _text(row: Mapping[str, object], key: str) -> str:
    if key in row and row[key] is None:
        return ""
    if key not in row or not isinstance(row[key], str):
        raise auth_error.InvalidDorisPermissionError(
            detail=f"Doris 授权结果缺少有效的 {key} 字段"
        )
    return str(row[key]).strip()


def _privileges(value: str) -> tuple[str, ...]:
    if value in {"", "NULL"}:
        return ()
    tokens = tuple(sorted(item.strip().lower() for item in value.split(",")))
    if any(not re.fullmatch(r"[a-z_]+_priv", token) for token in tokens):
        raise auth_error.InvalidDorisPermissionError(detail="无法解析 Doris 权限标识")
    return tokens


def parse_authorization(
    row: Mapping[str, object],
    policies: Sequence[Mapping[str, object]],
    *,
    role_name: str,
    query_user: str,
    data_source: str,
    catalog: str,
    database: str,
) -> DorisAuthorizationSnapshot:
    """严格解析有效权限；无法识别的格式拒绝使用，不回退到旧策略。"""
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
    broad = "select_priv" in global_privileges
    if broad:
        grants.add(DorisSelectGrant(role_name, data_source, database))
    normalized: dict[str, object] = {
        "user": query_user,
        "role": role_name,
        "source": data_source,
        "catalog": catalog,
        "database": database,
        "global": global_privileges,
    }
    for field, arity in (
        ("CatalogPrivs", 1),
        ("DatabasePrivs", 2),
        ("TablePrivs", 3),
        ("ColPrivs", 3),
    ):
        raw = _text(row, field)
        entries: list[tuple[str, tuple[str, ...]]] = []
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
                entries.append((target, values))
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
                    if arity == 1:
                        broad = True
                    grants.add(
                        DorisSelectGrant(
                            role_name,
                            data_source,
                            database,
                            parts[2] if arity == 3 else None,
                        )
                    )
        normalized[field] = sorted(entries)
    # 排序消除 SHOW 返回顺序的不确定性；保留谓词、组合类型和绑定对象。
    normalized["policies"] = sorted(
        {
            json.dumps(
                {
                    key: _text(policy, key)
                    for key in (
                        "CatalogName",
                        "DbName",
                        "TableName",
                        "FilterType",
                        "WherePredicate",
                    )
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            for policy in policies
        }
    )
    fingerprint = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
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
        fingerprint=fingerprint,
        has_broad_select=broad,
    )
