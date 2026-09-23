"""只读分析 SQL 的确定性安全校验。"""

from typing import cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.errors import ParseError
from sqlglot.expressions.dml import DML

from app.query.models.validation import (
    QueryValidationResult,
)

# 拦截查询内部的写入、锁、Hint、变量赋值和参数占位符。
_FORBIDDEN_NODE_TYPES = (
    exp.DDL,
    DML,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Hint,
    exp.Parameter,
    exp.SessionParameter,
    exp.Placeholder,
    exp.PropertyEQ,
)
# 即使出现在 SELECT 中也禁止的副作用函数。
_SIDE_EFFECT_FUNCTIONS = frozenset(
    {
        "benchmark",
        "get_lock",
        "load_file",
        "master_pos_wait",
        "release_all_locks",
        "release_lock",
        "sleep",
        "sys_exec",
        "sys_eval",
    }
)


class QueryGuardService:
    """解析 SQL 并校验只读语法和危险操作。"""

    def check(self, sql: str) -> QueryValidationResult:
        """检查单条只读 SQL；目录可见范围由 Doris 控制。"""
        expression, issues = self._parse_single_query(sql)
        if expression is None:
            return QueryValidationResult(
                valid=not issues, normalized_sql=None, issues=issues
            )

        if isinstance(expression, exp.Show):
            if str(expression.this).casefold() != "tables":
                issues.append("目录查询仅允许 SHOW TABLES")
        else:
            issues.extend(self._check_readonly(expression))
        return QueryValidationResult(
            valid=not issues,
            normalized_sql=(
                expression.sql(dialect="doris", pretty=False) if not issues else None
            ),
            issues=issues,
        )

    @staticmethod
    def _parse_single_query(
        sql: str,
    ) -> tuple[Expr | None, list[str]]:
        """解析且限制输入中只有一条有效语句。"""
        if not sql.strip():
            return None, ["SQL 语句不能为空"]
        try:
            parsed = sqlglot.parse(sql, read="doris")
        except ParseError as exc:
            return None, [f"SQL 语法解析失败: {exc}"]
        # 分号和尾部注释可能产生空节点，不算作独立语句。
        statements = [
            statement
            for statement in parsed
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
        if len(statements) != 1:
            return None, ["仅允许执行单条 SQL 语句"]
        return cast(Expr, statements[0]), []

    @staticmethod
    def _check_readonly(expression: Expr) -> list[str]:
        """检查语句类型、危险节点和有副作用的函数。"""
        issues: list[str] = []
        if not isinstance(expression, exp.Query) or expression.find(exp.Select) is None:
            issues.append("仅允许执行 SELECT 或 WITH 只读查询语句")
            return issues
        forbidden_keys = sorted(
            {
                node.key
                for node in expression.walk()
                if isinstance(node, _FORBIDDEN_NODE_TYPES)
                or (
                    isinstance(node.parent, (exp.CTE, exp.Subquery))
                    and node is node.parent.this
                    and not isinstance(node, (exp.Query, exp.Values))
                )
            }
        )
        if forbidden_keys:
            issues.append("查询包含禁止的操作: " + ", ".join(forbidden_keys))
        anonymous_functions = {
            function.name.casefold() for function in expression.find_all(exp.Anonymous)
        }
        forbidden_functions = sorted(anonymous_functions & _SIDE_EFFECT_FUNCTIONS)
        if forbidden_functions:
            issues.append("查询包含禁止的函数: " + ", ".join(forbidden_functions))
        return issues
