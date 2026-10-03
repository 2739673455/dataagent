"""只读 SQL 的静态规则检查与查询依赖收集。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import Expr, exp
from sqlglot.errors import OptimizeError, ParseError, SchemaError
from sqlglot.expressions.dml import DML
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.metadata.contracts import QueryCatalogColumn, QueryCatalogSnapshot
from app.query.contracts import (
    QueryColumnRef,
    QueryKind,
    QueryTableRef,
    QueryValidationIssue,
    QueryValidationResult,
)


@dataclass(frozen=True, slots=True)
class _Catalog:
    """用于辅助解析查询依赖的元数据目录快照。"""

    table_names: dict[str, str]
    columns: dict[str, dict[str, QueryCatalogColumn]]

    @property
    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        """构造 sqlglot 单数据库字段类型映射。"""
        # 例如 {"orders": {"id": "BIGINT"}}；外层数据库名由 _qualify 补充。
        return {
            self.table_names[table_key]: {
                column.name: column.type for column in columns.values()
            }
            for table_key, columns in self.columns.items()
        }


class QueryGuardService:
    """检查单条查询的语法限制，并收集业务查询的表、字段引用。"""

    def __init__(
        self,
        catalog_loader: Callable[[], Awaitable[QueryCatalogSnapshot]],
        *,
        current_database: str,
    ) -> None:
        """通过目录读取用例按需加载 SQL 校验所需的快照。"""
        self._catalog_loader = catalog_loader
        self._current_database = current_database

    async def check(
        self,
        sql: str,
    ) -> QueryValidationResult:
        """检查本地查询规则，并返回执行 SQL 与可解析的依赖信息。"""
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

        catalog = await self._load_catalog()
        raw_tables, table_issues = self._resolve_tables(expression, catalog)
        issues.extend(table_issues)
        if issues:
            return self._result(None, issues, tables=raw_tables)

        try:
            qualified = self._qualify(expression, catalog)
        except (OptimizeError, SchemaError):
            # 目录结构或字段补全失败时，使用未经补全的语法树继续分析。
            qualified = expression
        columns = self._collect_physical_columns(qualified, catalog)
        issues.extend(self._check_joins(qualified))

        return self._result(
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            tables=raw_tables,
            columns=columns,
            output_columns=list(cast(exp.Query, expression).named_selects),
        )

    @staticmethod
    def _parse_single_query(
        sql: str,
    ) -> tuple[Expr | None, list[QueryValidationIssue]]:
        """解析且限制输入中只有一条有效语句。"""
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
        """检查 SHOW TABLES 类型并返回目录查询结果。"""
        issues: list[QueryValidationIssue] = []
        if str(expression.this).casefold() != "tables":
            issues.append(
                QueryValidationIssue(
                    code="catalog_statement_not_allowed",
                    message="目录查询仅允许 SHOW TABLES",
                )
            )
        return self._result(
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            query_kind="catalog",
        )

    @staticmethod
    def _references_information_schema(expression: Expr) -> bool:
        """扫描所有表节点，判断是否引用 information_schema。"""
        return any(
            table.db.casefold() == "information_schema"
            for table in expression.find_all(exp.Table)
        )

    def _check_information_schema_query(
        self,
        expression: Expr,
    ) -> QueryValidationResult:
        """检查目录查询的只读语法，并拒绝任一表引用显式指定 Catalog。"""
        issues = self._check_readonly(expression)
        if issues:
            return self._result(None, issues, query_kind="catalog")
        if any(table.catalog for table in expression.find_all(exp.Table)):
            issues.append(
                QueryValidationIssue(
                    code="catalog_not_allowed",
                    message="information_schema 查询不允许指定 Catalog",
                )
            )
        return self._result(
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            output_columns=list(cast(exp.Query, expression).named_selects),
            query_kind="catalog",
        )

    @staticmethod
    def _check_readonly(expression: Expr) -> list[QueryValidationIssue]:
        """检查 SELECT 查询结构、禁止的节点类型和 Anonymous 函数白名单。"""
        issues: list[QueryValidationIssue] = []
        # 仅接受包含 SELECT 的查询结构，排除 INSERT ... SELECT 等写语句。
        if not isinstance(expression, exp.Query) or expression.find(exp.Select) is None:
            issues.append(
                QueryValidationIssue(
                    code="readonly_query_required",
                    message="仅允许执行 SELECT 或 WITH 只读查询语句",
                )
            )
            return issues
        # 遍历整棵树拦截禁止节点，以及 CTE/子查询中非 Query、非 Values 的主体。
        forbidden_keys = sorted(
            {
                node.key
                for node in expression.walk()
                if isinstance(
                    node,
                    (
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
                    ),
                )
                or (
                    isinstance(node.parent, (exp.CTE, exp.Subquery))
                    and node is node.parent.this
                    and not isinstance(node, (exp.Query, exp.Values))
                )
            }
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
        # 白名单仅约束未映射到专用 AST 节点的函数调用。
        unapproved_functions = sorted(
            anonymous_functions
            - {"curdate", "current_date", "current_time", "current_timestamp", "now"}
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

    async def _load_catalog(self) -> _Catalog:
        """收到普通目录快照后建立忽略大小写的查询映射。"""
        snapshot = await self._catalog_loader()
        table_names = {name.casefold(): name for name in snapshot.table_names}
        columns: dict[str, dict[str, QueryCatalogColumn]] = {
            table: {} for table in table_names
        }
        for column in snapshot.columns:
            table = column.t_name.casefold()
            if table in columns:
                columns[table][column.name.casefold()] = column
        return _Catalog(table_names, columns)

    def _resolve_tables(
        self,
        expression: Expr,
        catalog: _Catalog,
    ) -> tuple[list[QueryTableRef], list[QueryValidationIssue]]:
        """收集、去重并排序物理表引用，报告显式指定的 Catalog。"""
        table_refs: dict[str, QueryTableRef] = {}
        issues: list[QueryValidationIssue] = []
        for scope in traverse_scope(expression):
            for table_ref in self._physical_sources(scope, catalog, issues).values():
                table_refs[table_ref.qualified_name.casefold()] = table_ref
        return (
            sorted(
                table_refs.values(), key=lambda table: table.qualified_name.casefold()
            ),
            issues,
        )

    def _qualify(
        self,
        expression: Expr,
        catalog: _Catalog,
    ) -> exp.Query:
        """尝试补全分析副本中的字段来源，解析失败由调用方回退到原树。"""
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
                # 展开别名和星号以收集目录中已知的字段依赖。
                expand_alias_refs=True,
                expand_stars=True,
                infer_schema=False,
                validate_qualify_columns=True,
                quote_identifiers=False,
                identify=False,
            ),
        )

    def _collect_physical_columns(
        self,
        expression: Expr,
        catalog: _Catalog,
    ) -> list[QueryColumnRef]:
        """收集能够定位来源的物理字段。"""
        references: dict[str, QueryColumnRef] = {}
        # 各作用域分别收集字段，包含 SELECT、WHERE、JOIN 等位置的引用。
        for scope in traverse_scope(expression):
            physical_sources = self._physical_sources(scope, catalog)
            for column in scope.columns:
                table_ref = physical_sources.get(column.table.casefold())
                # 未限定字段仅在作用域只有一个来源且该来源为物理表时归属到该表。
                if not column.table and len(scope.selected_sources) == 1:
                    table_ref = next(iter(physical_sources.values()), None)
                if table_ref is None:
                    # 无法定位到本层物理表的字段跳过；CTE/派生表内部由其自身作用域处理。
                    continue
                column_info = (
                    catalog.columns.get(table_ref.name.casefold(), {}).get(
                        column.name.casefold()
                    )
                    if (table_ref.database or self._current_database).casefold()
                    == self._current_database.casefold()
                    else None
                )
                reference = QueryColumnRef(
                    database=table_ref.database,
                    table=table_ref.name,
                    name=column_info.name if column_info else column.name,
                )
                # 同一字段可在多个表达式中使用，资产依赖只需保留一次。
                references[reference.qualified_name.casefold()] = reference
        return sorted(
            references.values(),
            key=lambda column: column.qualified_name.casefold(),
        )

    @classmethod
    def _check_joins(cls, expression: Expr) -> list[QueryValidationIssue]:
        """拒绝 CROSS JOIN；要求 ON 或 USING，并检查 ON 中的跨来源比较。"""
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
                # 非 CROSS JOIN 至少需要 ON 或非空 USING。
                elif on is None and not using:
                    issues.append(
                        QueryValidationIssue(
                            code="join_condition_required",
                            message=f"JOIN 连接必须提供 ON 或 USING 关联条件: {right_alias}",
                            table=right_alias,
                        )
                    )
                elif on is not None and (
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
                # 已处理的右侧来源可参与后续 JOIN 的左侧比较。
                left_aliases.add(right_alias)
        return issues

    def _physical_sources(
        self,
        scope: Scope,
        catalog: _Catalog,
        issues: list[QueryValidationIssue] | None = None,
    ) -> dict[str, QueryTableRef]:
        """映射作用域别名到物理表；传入 issues 时报告显式 Catalog。"""
        sources: dict[str, QueryTableRef] = {}
        for alias, (_, source) in scope.selected_sources.items():
            # CTE/派生表的 source 是 Scope；其内部物理表会在遍历对应作用域时处理。
            if not isinstance(source, exp.Table):
                continue
            catalog_name = source.catalog
            database = source.db or self._current_database
            table_key = source.name.casefold()
            table_name = (
                catalog.table_names.get(table_key, source.name)
                if database.casefold() == self._current_database.casefold()
                else source.name
            )
            table_ref = QueryTableRef(database=database, name=table_name)
            if issues is not None and catalog_name:
                issues.append(
                    QueryValidationIssue(
                        code="catalog_not_allowed",
                        message=f"不允许访问外部 Catalog: {catalog_name}",
                        table=table_ref.qualified_name,
                    )
                )
            sources[alias.casefold()] = table_ref
        return sources

    @classmethod
    def _join_condition_links_sources(
        cls,
        condition: Expr,
        left_aliases: set[str],
        right_alias: str,
    ) -> bool:
        """递归检查跨来源比较：OR/XOR 要求各分支成立，其他包装节点取任一子项。"""
        if isinstance(
            condition,
            (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NullSafeEQ),
        ):
            return cls._comparison_links_sources(condition, left_aliases, right_alias)
        if isinstance(condition, (exp.Paren, exp.Not)):
            children = (condition.this,)
        elif isinstance(condition, (exp.And, exp.Or, exp.Xor)):
            children = (condition.this, condition.expression)
        else:
            children = condition.iter_expressions()

        matches = (
            cls._join_condition_links_sources(child, left_aliases, right_alias)
            for child in children
        )
        # OR/XOR 的每个分支都须关联两侧；AND 和其他包装表达式任一子项满足即可。
        return (
            all(matches) if isinstance(condition, (exp.Or, exp.Xor)) else any(matches)
        )

    @staticmethod
    def _comparison_links_sources(
        comparison: Expr,
        left_aliases: set[str],
        right_alias: str,
    ) -> bool:
        """根据带表名前缀的列，判断比较两侧是否分别属于前置来源和当前右侧来源。"""

        def source_side(operand: Expr) -> str | None:
            """根据操作数中带表名前缀的列归类来源。"""
            aliases = {
                column.table.casefold()
                for column in operand.find_all(exp.Column)
                if column.table
            }
            # 收集到的表名前缀必须全部归属同一侧。
            # 无可用前缀、混合两侧或包含无关别名时返回 None。
            if aliases and aliases <= left_aliases:
                return "left"
            if aliases == {right_alias}:
                return "right"
            return None

        # 集合比较使 a.id = b.id 与 b.id = a.id 等价，同时排除两边均来自同一侧。
        return {
            source_side(comparison.this),
            source_side(comparison.expression),
        } == {"left", "right"}

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
        """按问题首次出现的顺序去重并构造结果。"""
        distinct_issues = list(dict.fromkeys(issues))
        return QueryValidationResult(
            valid=not distinct_issues,
            normalized_sql=normalized_sql,
            query_kind=query_kind,
            tables=tables or [],
            columns=columns or [],
            output_columns=output_columns or [],
            issues=distinct_issues,
        )
