"""跨 Identity 与 Query 使用的 PostgreSQL 标识符与资源档位。"""

import re
from dataclasses import dataclass

BUSINESS_PG_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]{0,62}$"
BUSINESS_PG_WORKLOAD_GROUP_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$"


@dataclass(frozen=True, slots=True)
class BusinessPgResourceProfile:
    statement_timeout_seconds: int
    work_mem_kib: int
    connection_limit: int


BUSINESS_PG_RESOURCE_PROFILES = {
    "standard": BusinessPgResourceProfile(30, 4096, 5),
    "reporting": BusinessPgResourceProfile(120, 16384, 10),
}


def validate_business_pg_identifier(identifier: str) -> None:
    """禁止无效或会被 PostgreSQL 截断的角色标识符。"""
    if re.fullmatch(BUSINESS_PG_IDENTIFIER_PATTERN, identifier) is None:
        raise ValueError("PostgreSQL 标识符无效")
