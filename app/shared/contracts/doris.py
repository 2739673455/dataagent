"""跨 Identity 与 Query 使用的 Doris 标识符约束。"""

import re

DORIS_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$"
DORIS_WORKLOAD_GROUP_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


def validate_doris_identifier(identifier: str) -> None:
    """按管理入口的命名规则校验 Doris 标识符。"""
    if re.fullmatch(DORIS_IDENTIFIER_PATTERN, identifier) is None:
        raise ValueError("Doris 标识符无效")
