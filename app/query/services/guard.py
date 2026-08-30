"""只读分析 SQL 的确定性安全校验"""

import re
from dataclasses import dataclass
from typing import Protocol, cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.identity.services.authorization import AssetAccessPolicy, AssetIdentity
from app.metadata.models.catalog import ColumnInfo, TableInfo
from app.metadata.services.authorization_filter import MetadataAuthorizationFilter
from app.query.models.validation import (
    QueryColumnRef,
    QueryKind,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)


class QueryCatalogRepository(Protocol):
    """查询校验所需的元数据目录接口"""

    async def list_table_infos(self) -> list[TableInfo]:
        """列出参与查询校验的表元数据"""
        ...

    async def list_column_infos(self) -> list[ColumnInfo]:
        """列出参与查询校验的字段元数据"""
        ...


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
_ALLOWED_CATALOG_TABLES = frozenset({"columns", "tables"})
_COMPARISON_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NullSafeEQ,
)


class QueryGuardService:
    """解析 SQL 并校验只读、元数据、关联和资产权限"""

    def __init__(
        self,
        catalog_repo: QueryCatalogRepository,
        *,
        data_source: str,
        current_database: str,
    ) -> None:
        """初始化查询安全服务"""
        self._catalog_repo = catalog_repo
        self._data_source = data_source
        self._current_database = current_database

    async def check(
        self,
        sql: str,
        policy: AssetAccessPolicy | None = None,
    ) -> QueryValidationResult:
        """返回 SQL 的完整安全检查结果"""
        expression, issues = self._parse_single_query(sql)
        if expression is None:
            return self._result(None, issues)

        if isinstance(expression, exp.Show):
            return self._check_show_tables(expression)
        if self._references_information_schema(expression):
            return self._check_information_schema_query(expression)

        issues.extend(self._check_readonly(expression))
        if issues:
            return self._result(None, issues)

        catalog = await self._load_catalog(policy)
        raw_tables, star_tables, table_issues = self._resolve_tables(
            expression,
            catalog,
        )
        issues.extend(table_issues)
        issues.extend(self._check_restricted_stars(catalog, raw_tables, star_tables))
        if issues:
            return self._result(None, issues, tables=raw_tables)

        try:
            qualified = self._qualify(expression, catalog)
        except OptimizeError as exc:
            issue = self._optimization_issue(expression, catalog, exc)
            return self._result(None, [issue], tables=raw_tables)

        columns = self._collect_physical_columns(qualified, catalog)
        issues.extend(self._check_joins(qualified))
        output_columns = list(qualified.named_selects)
        duplicate_outputs = self._duplicates(output_columns)
        if duplicate_outputs:
            issues.append(
                QueryValidationIssue(
                    code="duplicate_output_column",
                    message=("查询输出列名不能重复: " + ", ".join(duplicate_outputs)),
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

        normalized_sql = qualified.sql(dialect="doris", pretty=False)
        return self._result(
            normalized_sql if not issues else None,
            issues,
            tables=raw_tables,
            columns=columns,
            output_columns=output_columns,
        )

    @staticmethod
    def _result(
        normalized_sql: str | None,
        issues: list[QueryValidationIssue],
        *,
        tables: list[QueryTableRef] | None = None,
        columns: list[QueryColumnRef] | None = None,
        output_columns: list[str] | None = None,
        query_kind: QueryKind = "business",
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
            normalized_sql=normalized_sql,
            query_kind=query_kind,
            tables=tables or [],
            columns=columns or [],
            output_columns=output_columns or [],
            issues=distinct_issues,
        )

    @staticmethod
    def _parse_single_query(
        sql: str,
    ) -> tuple[Expr | None, list[QueryValidationIssue]]:
        """解析且限制输入中只有一条有效语句"""
        if not sql.strip():
            return None, [
                QueryValidationIssue(code="empty_sql", message="SQL 语句不能为空")
            ]
        try:
            parsed = sqlglot.parse(sql, read="doris")
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

    def _check_show_tables(self, expression: exp.Show) -> QueryValidationResult:
        """仅允许查看当前业务数据库中当前角色可见的表"""
        issues: list[QueryValidationIssue] = []
        if str(expression.this).casefold() != "tables":
            issues.append(
                QueryValidationIssue(
                    code="catalog_statement_not_allowed",
                    message="目录查询仅允许 SHOW TABLES",
                )
            )
        unsupported = sorted(
            key
            for key, value in expression.args.items()
            if key not in {"this", "full", "db", "like", "json"}
            and value is not None
            and value is not False
        )
        if unsupported:
            issues.append(
                QueryValidationIssue(
                    code="catalog_statement_not_allowed",
                    message="SHOW TABLES 包含不支持的选项: " + ", ".join(unsupported),
                )
            )
        database = expression.args.get("db")
        if database is not None and (
            not isinstance(database, exp.Identifier)
            or database.name.casefold() != self._current_database.casefold()
        ):
            issues.append(
                QueryValidationIssue(
                    code="unknown_database",
                    message=(
                        "SHOW TABLES 只能查看当前业务数据库: "
                        f"{self._current_database}"
                    ),
                )
            )
        return self._result(
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            query_kind="catalog",
        )

    @staticmethod
    def _references_information_schema(expression: Expr) -> bool:
        """判断查询是否直接引用 information_schema"""
        return any(
            table.db.casefold() == "information_schema"
            for table in expression.find_all(exp.Table)
        )

    def _check_information_schema_query(
        self,
        expression: Expr,
    ) -> QueryValidationResult:
        """校验当前数据库下受限的 Doris 系统目录查询"""
        issues = self._check_readonly(expression)
        if not isinstance(expression, exp.Select):
            issues.append(
                QueryValidationIssue(
                    code="catalog_query_shape_not_allowed",
                    message="information_schema 仅允许单层 SELECT 查询",
                )
            )
            return self._result(None, issues, query_kind="catalog")

        from_expression = expression.args.get("from_")
        source = from_expression.this if from_expression is not None else None
        physical_tables = list(expression.find_all(exp.Table))
        if (
            not isinstance(source, exp.Table)
            or len(physical_tables) != 1
            or expression.args.get("joins")
            or expression.args.get("with_")
        ):
            issues.append(
                QueryValidationIssue(
                    code="catalog_query_shape_not_allowed",
                    message=(
                        "information_schema 仅允许直接查询一张系统目录表，"
                        "不允许 JOIN、CTE 或子查询"
                    ),
                )
            )
        elif source.catalog:
            issues.append(
                QueryValidationIssue(
                    code="catalog_not_allowed",
                    message="information_schema 查询不允许指定 Catalog",
                )
            )
        elif source.db.casefold() != "information_schema" or (
            source.name.casefold() not in _ALLOWED_CATALOG_TABLES
        ):
            issues.append(
                QueryValidationIssue(
                    code="catalog_table_not_allowed",
                    message="information_schema 仅允许查询 tables 或 columns",
                )
            )

        if not self._has_current_database_filter(expression):
            issues.append(
                QueryValidationIssue(
                    code="catalog_scope_required",
                    message=(
                        "information_schema 查询必须使用 "
                        "table_schema = DATABASE() 或当前数据库名限制范围"
                    ),
                )
            )

        output_columns = list(expression.named_selects)
        duplicate_outputs = self._duplicates(output_columns)
        if duplicate_outputs:
            issues.append(
                QueryValidationIssue(
                    code="duplicate_output_column",
                    message=("查询输出列名不能重复: " + ", ".join(duplicate_outputs)),
                )
            )
        return self._result(
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            output_columns=output_columns,
            query_kind="catalog",
        )

    def _has_current_database_filter(self, expression: exp.Select) -> bool:
        """确认系统目录查询通过 AND 条件限定到当前数据库"""
        where = expression.args.get("where")
        if where is None:
            return False

        def terms(condition: Expr) -> list[Expr]:
            if isinstance(condition, exp.Paren):
                return terms(condition.this)
            if isinstance(condition, exp.And):
                return terms(condition.this) + terms(condition.expression)
            return [condition]

        for condition in terms(where.this):
            if isinstance(condition, exp.Paren):
                condition = condition.this
            if not isinstance(condition, exp.EQ):
                continue
            for column, value in (
                (condition.this, condition.expression),
                (condition.expression, condition.this),
            ):
                if not (
                    isinstance(column, exp.Column)
                    and column.name.casefold() == "table_schema"
                ):
                    continue
                if isinstance(value, exp.CurrentSchema):
                    return True
                if (
                    isinstance(value, exp.Literal)
                    and value.is_string
                    and value.this.casefold() == self._current_database.casefold()
                ):
                    return True
        return False

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
                    message=("查询包含禁止的操作: " + ", ".join(forbidden_keys)),
                )
            )
        anonymous_functions = {
            function.name.casefold() for function in expression.find_all(exp.Anonymous)
        }
        forbidden_functions = sorted(anonymous_functions & _SIDE_EFFECT_FUNCTIONS)
        if forbidden_functions:
            issues.append(
                QueryValidationIssue(
                    code="forbidden_function",
                    message=("查询包含禁止的函数: " + ", ".join(forbidden_functions)),
                )
            )
        unapproved_functions = sorted(
            anonymous_functions - _SIDE_EFFECT_FUNCTIONS - _SAFE_ANONYMOUS_FUNCTIONS
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

    async def _load_catalog(
        self,
        policy: AssetAccessPolicy | None,
    ) -> _Catalog:
        """读取一次一致且已按用户授权收窄的目录快照"""
        table_infos = await self._catalog_repo.list_table_infos()
        column_infos = await self._catalog_repo.list_column_infos()
        restricted_star_tables: frozenset[str] = frozenset()
        if policy is not None:
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
        table_refs = {table.qualified_name.casefold(): table for table in tables}
        issues: list[QueryValidationIssue] = []
        for table_key in sorted(star_tables):
            table = table_refs.get(table_key)
            if (
                table is None
                or table.name.casefold() not in catalog.restricted_star_tables
            ):
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
    ) -> exp.Query:
        """基于元数据补全并验证字段、别名和 CTE 引用"""
        schema = catalog.sqlglot_schema
        if self._current_database:
            schema = {self._current_database: schema}
        return cast(
            exp.Query,
            qualify(
                expression.copy(),
                dialect="doris",
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
                if on is not None and (
                    not cls._join_condition_links_sources(
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
        """确认 JOIN 条件的布尔分支包含跨来源比较"""
        if isinstance(condition, exp.Paren):
            return cls._join_condition_links_sources(
                condition.this,
                left_aliases,
                right_alias,
            )
        if isinstance(condition, exp.Not):
            return cls._join_condition_links_sources(
                condition.this,
                left_aliases,
                right_alias,
            )
        if isinstance(condition, (exp.Or, exp.Xor)):
            return all(
                cls._join_condition_links_sources(
                    child,
                    left_aliases,
                    right_alias,
                )
                for child in (condition.this, condition.expression)
            )
        if isinstance(condition, exp.And):
            return any(
                cls._join_condition_links_sources(
                    child,
                    left_aliases,
                    right_alias,
                )
                for child in (condition.this, condition.expression)
            )
        if isinstance(condition, _COMPARISON_TYPES):
            return cls._comparison_links_sources(
                condition,
                left_aliases,
                right_alias,
            )
        return any(
            cls._join_condition_links_sources(
                child,
                left_aliases,
                right_alias,
            )
            for child in condition.iter_expressions()
        )

    @staticmethod
    def _comparison_links_sources(
        comparison: Expr,
        left_aliases: set[str],
        right_alias: str,
    ) -> bool:
        """判断比较操作的两侧分别只引用前置来源和当前右侧来源"""

        def source_side(operand: Expr) -> str | None:
            """判断一个比较操作数仅引用 Join 的哪一侧来源。"""
            aliases = {
                column.table.casefold()
                for column in operand.find_all(exp.Column)
                if column.table
            }
            if aliases and aliases <= left_aliases:
                return "left"
            if aliases == {right_alias}:
                return "right"
            return None

        return {
            source_side(comparison.this),
            source_side(comparison.expression),
        } == {"left", "right"}

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
