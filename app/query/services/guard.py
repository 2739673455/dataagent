"""只读分析 SQL 的确定性安全校验"""

import re
from dataclasses import dataclass
from typing import Protocol, cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models import ColumnInfo, TableInfo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.query.models import (
    QueryColumnRef,
    QueryDialect,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)


class QueryCatalogRepository(Protocol):
    """查询校验所需的元数据目录接口"""

    async def list_table_infos(self) -> list[TableInfo]: ...

    async def list_column_infos(self) -> list[ColumnInfo]: ...


class QueryAssetPolicyProvider(Protocol):
    """按用户加载查询资产策略"""

    async def get_asset_policy(self, user_id: int) -> AssetAccessPolicy: ...


@dataclass(frozen=True, slots=True)
class GuardedQuery:
    """通过全部检查且可交给执行层的 SQL"""

    sql: str
    validation: QueryValidationResult


class QueryRejectedError(ValueError):
    """SQL 未通过确定性安全校验"""

    def __init__(self, result: QueryValidationResult) -> None:
        self.result = result
        message = "; ".join(issue.message for issue in result.issues)
        super().__init__(message or "SQL 查询已被拒绝")


@dataclass(frozen=True, slots=True)
class _Catalog:
    """一次校验使用的元数据目录快照"""

    table_names: dict[str, str]
    columns: dict[str, dict[str, ColumnInfo]]
    restricted_star_tables: frozenset[str] = frozenset()

    @property
    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        """构造 sqlglot 单数据库字段类型映射"""
        return {
            self.table_names[table_key]: {
                column.name: column.type for column in columns.values()
            }
            for table_key, columns in self.columns.items()
        }


_FORBIDDEN_NODE_KEYS = frozenset(
    {
        "alter",
        "analyze",
        "cache",
        "command",
        "commit",
        "copy",
        "create",
        "delete",
        "describe",
        "drop",
        "execute",
        "grant",
        "hint",
        "insert",
        "into",
        "load_data",
        "lock",
        "merge",
        "parameter",
        "placeholder",
        "pragma",
        "propertyeq",
        "replace",
        "revoke",
        "rollback",
        "set",
        "sessionparameter",
        "show",
        "transaction",
        "truncate_table",
        "uncache",
        "update",
        "use",
    }
)
_SIDE_EFFECT_FUNCTIONS = frozenset(
    {
        "benchmark",
        "get_lock",
        "is_free_lock",
        "is_used_lock",
        "load_file",
        "master_pos_wait",
        "name_const",
        "release_all_locks",
        "release_lock",
        "sleep",
        "sys_exec",
        "sys_eval",
    }
)
_SAFE_ANONYMOUS_FUNCTIONS = frozenset(
    {
        "curdate",
        "current_date",
        "current_time",
        "current_timestamp",
        "now",
    }
)
_COMPARISON_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NullSafeEQ,
)
_ARITHMETIC_TYPES = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
_NUMERIC_TYPES = frozenset(
    {
        "BIGINT",
        "DECIMAL",
        "DOUBLE",
        "FLOAT",
        "INT",
        "LARGEINT",
        "MEDIUMINT",
        "SMALLINT",
        "TINYINT",
        "UBIGINT",
        "UINT",
        "USMALLINT",
        "UTINYINT",
    }
)
_STRING_TYPES = frozenset(
    {"CHAR", "LONGTEXT", "MEDIUMTEXT", "STRING", "TEXT", "TINYTEXT", "VARCHAR"}
)
_TEMPORAL_TYPES = frozenset(
    {
        "DATE",
        "DATE32",
        "DATETIME",
        "DATETIME64",
        "TIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
    }
)
_BINARY_TYPES = frozenset(
    {"BINARY", "BLOB", "LONGBLOB", "MEDIUMBLOB", "TINYBLOB", "VARBINARY"}
)


class QueryGuardService:
    """解析 SQL 并校验只读、元数据、关联和资产权限"""

    def __init__(
        self,
        catalog_repo: QueryCatalogRepository,
        *,
        data_source: str,
        current_database: str,
        max_cell_bytes: int,
        policy_provider: QueryAssetPolicyProvider | None = None,
    ) -> None:
        """初始化查询安全服务"""
        self._catalog_repo = catalog_repo
        self._data_source = data_source
        self._current_database = current_database
        if max_cell_bytes <= 0:
            raise ValueError("max_cell_bytes 必须为正整数")
        self._max_cell_bytes = max_cell_bytes
        self._policy_provider = policy_provider

    async def check(
        self,
        user_id: int,
        sql: str,
        dialect: QueryDialect = "doris",
    ) -> QueryValidationResult:
        """返回 SQL 的完整安全检查结果"""
        expression, issues = self._parse_single_query(sql, dialect)
        if expression is None:
            return self._result(dialect, None, issues)

        issues.extend(self._check_readonly(expression))
        issues.extend(self._check_value_expansion(expression))
        if issues:
            return self._result(dialect, None, issues)

        policy = (
            await self._policy_provider.get_asset_policy(user_id)
            if self._policy_provider is not None
            else None
        )
        catalog = await self._load_catalog(policy)
        raw_tables, star_tables, table_issues = self._resolve_tables(
            expression,
            catalog,
        )
        issues.extend(table_issues)
        issues.extend(self._check_restricted_stars(catalog, raw_tables, star_tables))
        if issues:
            return self._result(dialect, None, issues, tables=raw_tables)

        try:
            qualified = self._qualify(expression, catalog, dialect)
        except OptimizeError as exc:
            issue = self._optimization_issue(expression, catalog, exc)
            return self._result(dialect, None, [issue], tables=raw_tables)

        columns = self._collect_physical_columns(qualified, catalog)
        issues.extend(self._check_joins(qualified))
        issues.extend(self._check_types(qualified, catalog, dialect))
        output_columns = list(qualified.named_selects)
        duplicate_outputs = self._duplicates(output_columns)
        if duplicate_outputs:
            issues.append(
                QueryValidationIssue(
                    code="duplicate_output_column",
                    message=(
                        "查询输出列名不能重复: "
                        + ", ".join(duplicate_outputs)
                    ),
                )
            )

        if policy is not None:
            issues.extend(
                self._check_asset_policy(
                    policy,
                    raw_tables,
                    columns,
                    star_tables,
                )
            )

        normalized_sql = qualified.sql(dialect=dialect, pretty=False)
        return self._result(
            dialect,
            normalized_sql if not issues else None,
            issues,
            tables=raw_tables,
            columns=columns,
            output_columns=output_columns,
        )

    async def require_safe(
        self,
        user_id: int,
        sql: str,
        dialect: QueryDialect = "doris",
    ) -> GuardedQuery:
        """返回可执行 SQL 并在校验失败时拒绝查询"""
        result = await self.check(user_id, sql, dialect)
        if not result.valid or result.normalized_sql is None:
            raise QueryRejectedError(result)
        return GuardedQuery(sql=result.normalized_sql, validation=result)

    @staticmethod
    def _result(
        dialect: QueryDialect,
        normalized_sql: str | None,
        issues: list[QueryValidationIssue],
        *,
        tables: list[QueryTableRef] | None = None,
        columns: list[QueryColumnRef] | None = None,
        output_columns: list[str] | None = None,
    ) -> QueryValidationResult:
        """构造稳定排序并去重的校验结果"""
        distinct_issues = list(
            {
                (issue.code, issue.message, issue.table, issue.column): issue
                for issue in issues
            }.values()
        )
        return QueryValidationResult(
            valid=not distinct_issues,
            dialect=dialect,
            normalized_sql=normalized_sql,
            tables=tables or [],
            columns=columns or [],
            output_columns=output_columns or [],
            issues=distinct_issues,
        )

    @staticmethod
    def _parse_single_query(
        sql: str,
        dialect: QueryDialect,
    ) -> tuple[Expr | None, list[QueryValidationIssue]]:
        """解析且限制输入中只有一条有效语句"""
        if not sql.strip():
            return None, [
                QueryValidationIssue(code="empty_sql", message="SQL 语句不能为空")
            ]
        try:
            parsed = sqlglot.parse(sql, read=dialect)
        except ParseError as exc:
            return None, [
                QueryValidationIssue(
                    code="syntax_error",
                    message=f"SQL 语法解析失败: {exc}",
                )
            ]
        statements = [
            statement
            for statement in parsed
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
        if len(statements) != 1:
            return None, [
                QueryValidationIssue(
                    code="multiple_statements",
                    message="仅允许执行单条 SQL 语句",
                )
            ]
        return cast(Expr, statements[0]), []

    @staticmethod
    def _check_readonly(expression: Expr) -> list[QueryValidationIssue]:
        """检查语句类型、危险节点和有副作用的函数"""
        issues: list[QueryValidationIssue] = []
        if not isinstance(expression, exp.Query) or expression.find(exp.Select) is None:
            issues.append(
                QueryValidationIssue(
                    code="readonly_query_required",
                    message="仅允许执行 SELECT 或 WITH 只读查询语句",
                )
            )
            return issues
        forbidden_keys = sorted(
            {node.key for node in expression.walk() if node.key in _FORBIDDEN_NODE_KEYS}
        )
        if forbidden_keys:
            issues.append(
                QueryValidationIssue(
                    code="forbidden_operation",
                    message=(
                        "查询包含禁止的操作: "
                        + ", ".join(forbidden_keys)
                    ),
                )
            )
        anonymous_functions = {
            function.name.casefold()
            for function in expression.find_all(exp.Anonymous)
        }
        forbidden_functions = sorted(
            anonymous_functions & _SIDE_EFFECT_FUNCTIONS
        )
        if forbidden_functions:
            issues.append(
                QueryValidationIssue(
                    code="forbidden_function",
                    message=(
                        "查询包含禁止的函数: "
                        + ", ".join(forbidden_functions)
                    ),
                )
            )
        unapproved_functions = sorted(
            anonymous_functions
            - _SIDE_EFFECT_FUNCTIONS
            - _SAFE_ANONYMOUS_FUNCTIONS
        )
        if unapproved_functions:
            issues.append(
                QueryValidationIssue(
                    code="unapproved_function",
                    message=(
                        "查询包含未经授权的非白名单函数: "
                        + ", ".join(unapproved_functions)
                    ),
                )
            )
        return issues

    def _check_value_expansion(
        self,
        expression: Expr,
    ) -> list[QueryValidationIssue]:
        """拒绝可静态推导为超大单元格的字符串扩展"""
        oversized: set[str] = set()
        for function in expression.find_all(exp.Repeat, exp.Pad, exp.Space):
            target_length: int | None = None
            if isinstance(function, exp.Repeat):
                count = self._literal_nonnegative_int(function.args.get("times"))
                source = function.this
                if count is not None and isinstance(source, exp.Literal):
                    target_length = len(source.name.encode("utf-8")) * count
                elif count is not None:
                    target_length = count
            elif isinstance(function, exp.Pad):
                target_length = self._literal_nonnegative_int(
                    function.args.get("expression")
                )
            elif isinstance(function, exp.Space):
                target_length = self._literal_nonnegative_int(function.this)
            if target_length is not None and target_length > self._max_cell_bytes:
                oversized.add(function.sql(dialect="doris"))
        if not oversized:
            return []
        return [
            QueryValidationIssue(
                code="value_expansion_too_large",
                message=(
                    "查询包含超出单单元格上限的字符串扩展操作: "
                    + ", ".join(sorted(oversized))
                ),
            )
        ]

    @staticmethod
    def _literal_nonnegative_int(value: object) -> int | None:
        """读取非负整数字面量"""
        if not isinstance(value, exp.Literal) or value.is_string:
            return None
        try:
            parsed = int(value.this)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    async def _load_catalog(
        self,
        policy: AssetAccessPolicy | None,
    ) -> _Catalog:
        """读取一次一致且已按用户授权收窄的目录快照"""
        table_infos = await self._catalog_repo.list_table_infos()
        column_infos = await self._catalog_repo.list_column_infos()
        restricted_star_tables: frozenset[str] = frozenset()
        if policy is not None and not policy.unrestricted:
            authorization_filter = MetadataAuthorizationFilter(
                policy,
                self._data_source,
                self._current_database,
            )
            allowed_column_keys = authorization_filter.allowed_column_keys(column_infos)
            table_infos = authorization_filter.filter_tables(
                table_infos,
                allowed_column_keys,
            )
            visible_table_names = {table.name for table in table_infos}
            restricted_star_tables = frozenset(
                table_name.casefold()
                for table_name in visible_table_names
                if any(
                    column.t_name == table_name
                    and (column.t_name, column.name) not in allowed_column_keys
                    for column in column_infos
                )
            )
            column_infos = authorization_filter.filter_columns(
                column_infos,
                allowed_column_keys,
            )
        table_names = {table.name.casefold(): table.name for table in table_infos}
        columns: dict[str, dict[str, ColumnInfo]] = {
            table_key: {} for table_key in table_names
        }
        for column in column_infos:
            table_key = column.t_name.casefold()
            if table_key in columns:
                columns[table_key][column.name.casefold()] = column
        return _Catalog(
            table_names=table_names,
            columns=columns,
            restricted_star_tables=restricted_star_tables,
        )

    @staticmethod
    def _check_restricted_stars(
        catalog: _Catalog,
        tables: list[QueryTableRef],
        star_tables: set[str],
    ) -> list[QueryValidationIssue]:
        """字段级授权不允许通过星号扩展隐藏字段"""
        table_refs = {
            table.qualified_name.casefold(): table
            for table in tables
        }
        issues: list[QueryValidationIssue] = []
        for table_key in sorted(star_tables):
            table = table_refs.get(table_key)
            if table is None or table.name.casefold() not in catalog.restricted_star_tables:
                continue
            issues.append(
                QueryValidationIssue(
                    code="column_access_denied",
                    message=(
                        "使用通配符 '*' 需要对该表的所有字段均具备访问权限: "
                        f"{table.qualified_name}"
                    ),
                    table=table.qualified_name,
                )
            )
        return issues

    def _resolve_tables(
        self,
        expression: Expr,
        catalog: _Catalog,
    ) -> tuple[list[QueryTableRef], set[str], list[QueryValidationIssue]]:
        """区分物理表与 CTE 并解析星号涉及的物理表"""
        table_refs: dict[str, QueryTableRef] = {}
        star_tables: set[str] = set()
        issues: list[QueryValidationIssue] = []
        for scope in traverse_scope(expression):
            physical_sources = self._physical_sources(scope, catalog, issues)
            for table_ref in physical_sources.values():
                table_refs[table_ref.qualified_name.casefold()] = table_ref
            if isinstance(scope.expression, exp.Query):
                for select in scope.expression.selects:
                    for star in select.find_all(exp.Star):
                        parent = star.parent
                        if isinstance(parent, exp.Column) and parent.table:
                            table_ref = physical_sources.get(parent.table.casefold())
                            if table_ref is not None:
                                star_tables.add(table_ref.qualified_name.casefold())
                            continue
                        star_tables.update(
                            table_ref.qualified_name.casefold()
                            for table_ref in physical_sources.values()
                        )
        return (
            sorted(
                table_refs.values(), key=lambda table: table.qualified_name.casefold()
            ),
            star_tables,
            issues,
        )

    def _physical_sources(
        self,
        scope: Scope,
        catalog: _Catalog,
        issues: list[QueryValidationIssue] | None = None,
    ) -> dict[str, QueryTableRef]:
        """解析作用域别名对应的物理表"""
        sources: dict[str, QueryTableRef] = {}
        for alias, (_, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table):
                continue
            catalog_name = source.catalog
            database = source.db or self._current_database
            table_key = source.name.casefold()
            table_name = catalog.table_names.get(table_key, source.name)
            table_ref = QueryTableRef(database=database, name=table_name)
            if issues is not None:
                if catalog_name:
                    issues.append(
                        QueryValidationIssue(
                            code="catalog_not_allowed",
                            message=f"不允许访问外部 Catalog: {catalog_name}",
                            table=table_ref.qualified_name,
                        )
                    )
                if database.casefold() != self._current_database.casefold():
                    issues.append(
                        QueryValidationIssue(
                            code="unknown_database",
                            message=f"数据库不在元数据目录管理范围内: {database}",
                            table=table_ref.qualified_name,
                        )
                    )
                if table_key not in catalog.table_names:
                    issues.append(
                        QueryValidationIssue(
                            code="unknown_table",
                            message=f"元数据目录中未找到指定表: {table_ref.qualified_name}",
                            table=table_ref.qualified_name,
                        )
                    )
            sources[alias.casefold()] = table_ref
        return sources

    def _qualify(
        self,
        expression: Expr,
        catalog: _Catalog,
        dialect: QueryDialect,
    ) -> exp.Query:
        """基于元数据补全并验证字段、别名和 CTE 引用"""
        schema = catalog.sqlglot_schema
        if self._current_database:
            schema = {self._current_database: schema}
        return cast(
            exp.Query,
            qualify(
                expression.copy(),
                dialect=dialect,
                db=self._current_database,
                schema=cast(dict[str, object], schema),
                expand_alias_refs=True,
                expand_stars=True,
                infer_schema=False,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            ),
        )

    def _optimization_issue(
        self,
        expression: Expr,
        catalog: _Catalog,
        error: OptimizeError,
    ) -> QueryValidationIssue:
        """把 sqlglot 字段解析错误转换为稳定错误码"""
        message = str(error)
        match = re.search(r"Column ['\"]([^'\"]+)", message)
        column_name = match.group(1) if match else None
        if column_name and self._is_ambiguous_column(expression, catalog, column_name):
            return QueryValidationIssue(
                code="ambiguous_column",
                message=f"存在歧义的列引用: {column_name}",
                column=column_name,
            )
        code = (
            "unknown_column" if "column" in message.casefold() else "invalid_reference"
        )
        return QueryValidationIssue(
            code=code,
            message=f"SQL 引用校验失败: {message}",
            column=column_name,
        )

    def _is_ambiguous_column(
        self,
        expression: Expr,
        catalog: _Catalog,
        column_name: str,
    ) -> bool:
        """判断未限定字段是否同时存在于多个当前作用域来源"""
        column_key = column_name.casefold()
        for scope in traverse_scope(expression):
            if not any(
                not column.table and column.name.casefold() == column_key
                for column in scope.columns
            ):
                continue
            candidates = 0
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    if column_key in catalog.columns.get(source.name.casefold(), {}):
                        candidates += 1
                elif isinstance(source.expression, exp.Query) and column_key in {
                    name.casefold() for name in source.expression.named_selects
                }:
                    candidates += 1
            if candidates > 1:
                return True
        return False

    def _collect_physical_columns(
        self,
        expression: Expr,
        catalog: _Catalog,
    ) -> list[QueryColumnRef]:
        """收集字段血缘中直接引用的物理字段"""
        references: dict[str, QueryColumnRef] = {}
        for scope in traverse_scope(expression):
            physical_sources = self._physical_sources(scope, catalog)
            for column in scope.columns:
                if not column.table:
                    continue
                table_ref = physical_sources.get(column.table.casefold())
                if table_ref is None:
                    continue
                column_info = catalog.columns[table_ref.name.casefold()].get(
                    column.name.casefold()
                )
                if column_info is None:
                    continue
                reference = QueryColumnRef(
                    database=table_ref.database,
                    table=table_ref.name,
                    name=column_info.name,
                )
                references[reference.qualified_name.casefold()] = reference
        return sorted(
            references.values(),
            key=lambda column: column.qualified_name.casefold(),
        )

    @classmethod
    def _check_joins(cls, expression: Expr) -> list[QueryValidationIssue]:
        """检查 JOIN 条件包含左右来源且避免隐式笛卡尔积"""
        issues: list[QueryValidationIssue] = []
        for scope in traverse_scope(expression):
            if not isinstance(scope.expression, exp.Select):
                continue
            joins = scope.expression.args.get("joins") or []
            left_aliases: set[str] = set()
            from_expression = scope.expression.args.get("from_")
            if from_expression is not None and from_expression.this is not None:
                left_aliases.add(from_expression.this.alias_or_name.casefold())
            for join in joins:
                right_alias = join.this.alias_or_name.casefold()
                kind = str(join.args.get("kind") or "").casefold()
                on = join.args.get("on")
                using = join.args.get("using") or []
                if kind == "cross":
                    issues.append(
                        QueryValidationIssue(
                            code="cross_join_forbidden",
                            message=f"不允许使用笛卡尔积 CROSS JOIN: {right_alias}",
                            table=right_alias,
                        )
                    )
                    left_aliases.add(right_alias)
                    continue
                if on is None and not using:
                    issues.append(
                        QueryValidationIssue(
                            code="join_condition_required",
                            message=f"JOIN 连接必须提供 ON 或 USING 关联条件: {right_alias}",
                            table=right_alias,
                        )
                    )
                    left_aliases.add(right_alias)
                    continue
                has_unsupported_boolean = on is not None and any(
                    on.find_all(exp.Or, exp.Not, exp.Xor)
                )
                if on is not None and (
                    has_unsupported_boolean
                    or not cls._join_condition_links_sources(
                        on,
                        left_aliases,
                        right_alias,
                    )
                ):
                    issues.append(
                        QueryValidationIssue(
                            code="invalid_join_condition",
                            message=(
                                "JOIN 条件必须同时关联当前连接源与前置数据源: "
                                f"{right_alias}"
                            ),
                            table=right_alias,
                        )
                    )
                left_aliases.add(right_alias)
        return issues

    @classmethod
    def _join_condition_links_sources(
        cls,
        condition: Expr,
        left_aliases: set[str],
        right_alias: str,
    ) -> bool:
        """确认 JOIN 条件每个可成立分支都包含跨来源谓词"""
        if isinstance(condition, exp.Paren):
            return cls._join_condition_links_sources(
                condition.this,
                left_aliases,
                right_alias,
            )
        if isinstance(condition, (exp.Not, exp.Or, exp.Xor)):
            return False
        if isinstance(condition, exp.And):
            child_results = (
                cls._join_condition_links_sources(
                    condition.this,
                    left_aliases,
                    right_alias,
                ),
                cls._join_condition_links_sources(
                    condition.expression,
                    left_aliases,
                    right_alias,
                ),
            )
            return any(child_results)
        if not isinstance(condition, _COMPARISON_TYPES):
            return False

        def source_side(operand: Expr) -> str | None:
            while isinstance(operand, exp.Paren):
                operand = operand.this
            if not isinstance(operand, exp.Column) or not operand.table:
                return None
            alias = operand.table.casefold()
            if alias == right_alias:
                return "right"
            if alias in left_aliases:
                return "left"
            return None

        return {
            source_side(condition.this),
            source_side(condition.expression),
        } == {"left", "right"}

    def _check_types(
        self,
        expression: Expr,
        catalog: _Catalog,
        dialect: QueryDialect,
    ) -> list[QueryValidationIssue]:
        """检查比较和算术表达式中的明显类型冲突"""
        schema = catalog.sqlglot_schema
        if self._current_database:
            schema = {self._current_database: schema}
        try:
            annotate_types(
                expression,
                schema=cast(dict[str, object], schema),
                dialect=dialect,
            )
        except (OptimizeError, ValueError):
            return []
        return self._find_type_issues(expression, dialect)

    @classmethod
    def _find_type_issues(
        cls,
        expression: Expr,
        dialect: QueryDialect,
    ) -> list[QueryValidationIssue]:
        """从已完成类型推导的 AST 中查找冲突"""
        issues: list[QueryValidationIssue] = []
        for comparison in expression.find_all(*_COMPARISON_TYPES):
            left_category = cls._type_category(comparison.this)
            right_category = cls._type_category(comparison.expression)
            if not cls._types_compatible(left_category, right_category):
                issues.append(
                    QueryValidationIssue(
                        code="incompatible_types",
                        message=(
                            f"比较操作两端数据类型不兼容 ({left_category} 与 {right_category}): "
                            f"{comparison.sql(dialect=dialect)}"
                        ),
                    )
                )
        for predicate in expression.find_all(exp.In):
            left_category = cls._type_category(predicate.this)
            for candidate in predicate.expressions:
                right_category = cls._type_category(candidate)
                if cls._types_compatible(left_category, right_category):
                    continue
                issues.append(
                    QueryValidationIssue(
                        code="incompatible_types",
                        message=(
                            f"IN 谓词数据类型不兼容 ({left_category} 与 {right_category}): "
                            f"{predicate.sql(dialect=dialect)}"
                        ),
                    )
                )
                break
        for predicate in expression.find_all(exp.Between):
            value_category = cls._type_category(predicate.this)
            bound_categories = (
                cls._type_category(predicate.args[argument])
                for argument in ("low", "high")
            )
            if all(
                cls._types_compatible(value_category, bound_category)
                for bound_category in bound_categories
            ):
                continue
            issues.append(
                QueryValidationIssue(
                    code="incompatible_types",
                    message=(
                        f"BETWEEN 谓词数据类型不兼容: {predicate.sql(dialect=dialect)}"
                    ),
                )
            )
        for arithmetic in expression.find_all(*_ARITHMETIC_TYPES):
            left_category = cls._type_category(arithmetic.this)
            right_category = cls._type_category(arithmetic.expression)
            if {left_category, right_category} <= {"numeric", "unknown", "null"}:
                continue
            if (
                isinstance(arithmetic, (exp.Add, exp.Sub))
                and "temporal" in {left_category, right_category}
                and "interval" in {left_category, right_category}
            ):
                continue
            issues.append(
                QueryValidationIssue(
                    code="incompatible_types",
                    message=(
                        "算术运算要求两侧为兼容的数值类型: "
                        f"{arithmetic.sql(dialect=dialect)}"
                    ),
                )
            )
        return issues

    @staticmethod
    def _type_category(expression: Expr) -> str:
        """把 sqlglot 推断类型归并为可比较类别"""
        if isinstance(expression, exp.Null):
            return "null"
        data_type = expression.type
        if not isinstance(data_type, exp.DataType):
            return "unknown"
        type_name = (
            data_type.this.name
            if hasattr(data_type.this, "name")
            else str(data_type.this)
        )
        type_name = type_name.upper()
        if type_name in _NUMERIC_TYPES:
            return "numeric"
        if type_name in _STRING_TYPES:
            return "string"
        if type_name in _TEMPORAL_TYPES:
            return "temporal"
        if type_name in _BINARY_TYPES:
            return "binary"
        if type_name == "BOOLEAN":
            return "boolean"
        if type_name == "INTERVAL":
            return "interval"
        if type_name in {"NULL", "UNKNOWN"}:
            return "null" if type_name == "NULL" else "unknown"
        return type_name.casefold()

    @staticmethod
    def _types_compatible(left: str, right: str) -> bool:
        """判断两侧类型是否适合直接比较"""
        if left in {"unknown", "null"} or right in {"unknown", "null"}:
            return True
        if left == right:
            return True
        return {left, right} == {"string", "temporal"}

    def _check_asset_policy(
        self,
        policy: AssetAccessPolicy,
        tables: list[QueryTableRef],
        columns: list[QueryColumnRef],
        star_tables: set[str],
    ) -> list[QueryValidationIssue]:
        """逐个检查星号表和显式物理字段的资产权限"""
        issues: list[QueryValidationIssue] = []
        columns_by_table: dict[str, list[QueryColumnRef]] = {}
        for column in columns:
            table_key = QueryTableRef(
                database=column.database,
                name=column.table,
            ).qualified_name.casefold()
            columns_by_table.setdefault(table_key, []).append(column)

        for table in tables:
            table_key = table.qualified_name.casefold()
            table_columns = columns_by_table.get(table_key, [])
            if table_key in star_tables or not table_columns:
                identity = AssetIdentity(
                    data_source=self._data_source,
                    database_name=table.database,
                    table_name=table.name,
                )
                if not policy.allows(identity):
                    issues.append(
                        QueryValidationIssue(
                            code="table_access_denied",
                            message=f"无权访问表: {table.qualified_name}",
                            table=table.qualified_name,
                        )
                    )
                if table_key in star_tables:
                    continue
            for column in table_columns:
                identity = AssetIdentity(
                    data_source=self._data_source,
                    database_name=column.database,
                    table_name=column.table,
                    column_name=column.name,
                )
                if not policy.allows(identity):
                    issues.append(
                        QueryValidationIssue(
                            code="column_access_denied",
                            message=f"无权访问字段: {column.qualified_name}",
                            table=table.qualified_name,
                            column=column.qualified_name,
                        )
                    )
        return issues

    @staticmethod
    def _duplicates(names: list[str]) -> list[str]:
        """返回忽略大小写后的重复输出名"""
        seen: set[str] = set()
        duplicates: dict[str, str] = {}
        for name in names:
            key = name.casefold()
            if key in seen:
                duplicates.setdefault(key, name)
            seen.add(key)
        return sorted(duplicates.values(), key=str.casefold)
