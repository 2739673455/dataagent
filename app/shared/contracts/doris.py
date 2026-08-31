"""跨 Identity 与 Query 使用的 Doris 标识符约束。"""

DORIS_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_$.-]{0,127}$"
DORIS_WORKLOAD_GROUP_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
