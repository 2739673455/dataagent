"""只读分析 SQL 的确定性安全校验。"""

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
    """查询校验所需的元数据目录接口。"""

    async def list_table_infos(self) -> list[TableInfo]:
        """列出参与查询校验的表元数据。"""
        ...

    async def list_column_infos(self) -> list[ColumnInfo]:
        """列出参与查询校验的字段元数据。"""
        ...


@dataclass(frozen=True, slots=True)
class _Catalog:
    """一次校验使用的元数据目录快照。"""

    table_names: dict[str, str]
    columns: dict[str, dict[str, ColumnInfo]]
    restricted_star_tables: frozenset[str] = frozenset()

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


# 这些是 SQLGlot AST 节点的 key，不是对原始 SQL 做关键词匹配。
# SELECT 内部也可能出现 INTO、锁、Hint 等节点，因此要遍历整棵树。
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
# 即使位于 SELECT 中，也不接受锁操作、文件访问、执行外部命令或主动耗时函数。
# 本集合在下方用于检查被 SQLGlot 解析为 Anonymous 的函数调用。
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
# Anonymous 指解析器未映射到专用函数节点的调用，不是 Python 的匿名函数。
# 对这一类函数采用白名单；正常解析为专用 AST 节点的内置函数不经过此白名单。
_SAFE_ANONYMOUS_FUNCTIONS = frozenset(
    {
        "curdate",
        "current_date",
        "current_time",
        "current_timestamp",
        "now",
    }
)
# 目录访问只开放发现业务表和字段所需的两张系统表。
_ALLOWED_CATALOG_TABLES = frozenset({"columns", "tables"})
# JOIN 不强制只用等值连接，也接受大小比较等跨来源条件。
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
    """解析 SQL 并校验只读、元数据、关联和资产权限。"""

    def __init__(
        self,
        catalog_repo: QueryCatalogRepository,
        *,
        data_source: str,
        current_database: str,
    ) -> None:
        """初始化查询安全服务。"""
        self._catalog_repo = catalog_repo
        self._data_source = data_source
        self._current_database = current_database

    async def check(
        self,
        sql: str,
        policy: AssetAccessPolicy | None = None,
    ) -> QueryValidationResult:
        """返回 SQL 的完整安全检查结果。"""
        # 1. 按 Doris 方言解析 SQL，检查输入非空、语法正确且只有一条有效语句。
        # 无法得到单条语句时直接返回，后续检查都依赖解析出的语法树。
        expression, issues = self._parse_single_query(sql)
        if expression is None:
            return self._result(None, issues)

        # 2. 目录查询走专门的白名单：SHOW 只允许查看当前库的表；
        # information_schema 查询限制目录表、查询结构和当前数据库过滤条件。
        # 这两类查询直接返回检查结果，实际目录可见范围由 Doris 查询账号控制。
        if isinstance(expression, exp.Show):
            return self._check_show_tables(expression)
        if self._references_information_schema(expression):
            return self._check_information_schema_query(expression)

        # 3. 检查业务 SQL 是否为只读查询，拦截写操作、锁、Hint、参数占位符、
        # 危险函数和未经允许的匿名函数。先检查语法，拒绝后无需再读取元数据。
        issues.extend(self._check_readonly(expression))
        if issues:
            return self._result(None, issues)

        # 4. 加载表和字段元数据；传入权限策略时，只保留当前用户可见的资源，
        # 并标记只有部分字段权限的表，供后续星号检查使用。
        catalog = await self._load_catalog(policy)
        # 5. 区分物理表、别名和 CTE，检查表是否在当前库的可见元数据目录中，
        # 拒绝显式 Catalog 和其他数据库，同时记录星号引用涉及哪些物理表。
        raw_tables, star_tables, table_issues = self._resolve_tables(
            expression,
            catalog,
        )
        issues.extend(table_issues)
        # 只有部分字段权限时，拒绝通过 * 或 table.* 读取整表字段。
        # 表引用或星号权限有问题时直接返回，避免继续用不完整的目录解析字段。
        issues.extend(self._check_restricted_stars(catalog, raw_tables, star_tables))
        if issues:
            return self._result(None, issues, tables=raw_tables)

        # 6. 根据目录补全字段所属表、展开别名和星号，检查字段及 CTE 引用。
        # 不存在或有歧义的字段转换成明确的校验问题；解析失败后停止后续检查。
        try:
            qualified = self._qualify(expression, catalog)
        except OptimizeError as exc:
            issue = self._optimization_issue(expression, catalog, exc)
            return self._result(None, [issue], tables=raw_tables)

        # 7. 收集 SELECT、WHERE、JOIN 等位置实际引用的物理字段，
        # 用于后续权限检查和返回查询所依赖的资产。
        columns = self._collect_physical_columns(qualified, catalog)
        # 8. 拒绝 CROSS JOIN；其他 JOIN 必须有 ON 或 USING，
        # 并检查 ON 中存在连接当前右侧来源与前置来源的跨表比较。
        issues.extend(self._check_joins(qualified))
        # 9. 检查最终输出列名，忽略大小写后也不能重复，避免 CSV 列名冲突。
        output_columns = list(qualified.named_selects)
        duplicate_outputs = self._duplicates(output_columns)
        if duplicate_outputs:
            issues.append(
                QueryValidationIssue(
                    code="duplicate_output_column",
                    message=("查询输出列名不能重复: " + ", ".join(duplicate_outputs)),
                )
            )

        # 10. 传入权限策略时，再逐项检查物理表和字段的访问权限。
        # 星号或没有显式字段的表访问要求表级授权，显式字段逐列校验。
        if policy is not None:
            issues.extend(
                self._check_asset_policy(
                    policy,
                    raw_tables,
                    columns,
                    star_tables,
                )
            )

        # 11. 将补全后的语法树转换为 Doris SQL；只有全部检查通过才返回可执行
        # 的 normalized_sql，有问题时返回问题列表和已解析出的表、字段信息。
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
        """按问题首次出现的顺序去重并构造结果。"""
        # 多个阶段可能发现同一问题；字典保留键的首次插入顺序，不按错误码排序。
        # valid 只根据 issues 决定，调用方负责在失败时不提供可执行 SQL。
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
        """解析且限制输入中只有一条有效语句。"""
        if not sql.strip():
            return None, [
                QueryValidationIssue(code="empty_sql", message="SQL 语句不能为空")
            ]
        try:
            # 使用解析器识别语句边界，不能直接 split(";")：字符串和注释中也可含分号。
            parsed = sqlglot.parse(sql, read="doris")
        except ParseError as exc:
            return None, [
                QueryValidationIssue(
                    code="syntax_error",
                    message=f"SQL 语法解析失败: {exc}",
                )
            ]
        # 多余分号可能产生 None；例如 SELECT 1; -- comment 还可能产生携带注释的
        # exp.Semicolon 节点。它们都不是独立 SQL，过滤后再统计有效语句数量。
        statements = [
            statement
            for statement in parsed
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
        # 既拒绝多条语句，也拒绝只包含注释或分隔符、没有有效语句的输入。
        if len(statements) != 1:
            return None, [
                QueryValidationIssue(
                    code="multiple_statements",
                    message="仅允许执行单条 SQL 语句",
                )
            ]
        return cast(Expr, statements[0]), []

    def _check_show_tables(self, expression: exp.Show) -> QueryValidationResult:
        """仅允许查看当前业务数据库中当前角色可见的表。"""
        issues: list[QueryValidationIssue] = []
        # Show.this 保存 SHOW 的目标，例如 "TABLES" 或 "DATABASES"；str 转为字符串，
        # casefold 统一大小写后比较。因此 SHOW TABLES 通过，SHOW DATABASES 等被拒绝。
        if str(expression.this).casefold() != "tables":
            issues.append(
                QueryValidationIssue(
                    code="catalog_statement_not_allowed",
                    message="目录查询仅允许 SHOW TABLES",
                )
            )
        # 对 SHOW 的语法选项使用允许列表；空值和 False 是未启用的解析器默认项。
        # 未指定数据库时依赖查询连接的默认库，指定时必须与配置的业务库一致。
        # args 是 AST 节点的结构化参数字典，不是 SQL 字符串：full 对应 FULL，
        # db 对应数据库名，like 对应 LIKE 条件。sorted 让问题列表的显示顺序固定。
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
        # Identifier 表示名称标识符；先确认节点类型，再比较其 name 与配置库名。
        # 不接受用其他类型的表达式充当数据库名，也不允许查看其他库。
        if database is not None and (
            not isinstance(database, exp.Identifier)
            or database.name.casefold() != self._current_database.casefold()
        ):
            issues.append(
                QueryValidationIssue(
                    code="unknown_database",
                    message=(
                        f"SHOW TABLES 只能查看当前业务数据库: {self._current_database}"
                    ),
                )
            )
        return self._result(
            # sql() 把 AST 重新生成 SQL；pretty=False 不做多行美化。存在问题则返回 None。
            expression.sql(dialect="doris", pretty=False) if not issues else None,
            issues,
            query_kind="catalog",
        )

    @staticmethod
    def _references_information_schema(expression: Expr) -> bool:
        """判断查询是否直接引用 information_schema。"""
        # 扫描所有层级的表节点：即使系统表藏在 CTE 或子查询中，也进入目录检查分支。
        # find_all(exp.Table) 遍历表节点；any 表示只要有一张表属于系统库就返回 True。
        return any(
            table.db.casefold() == "information_schema"
            for table in expression.find_all(exp.Table)
        )

    def _check_information_schema_query(
        self,
        expression: Expr,
    ) -> QueryValidationResult:
        """校验当前数据库下受限的 Doris 系统目录查询。"""
        # 目录 SQL 同样必须通过只读语法检查；允许访问系统表不代表放宽函数等限制。
        issues = self._check_readonly(expression)
        # isinstance 检查 AST 节点类型：顶层必须直接是 Select，不能是 Union 等组合查询。
        if not isinstance(expression, exp.Select):
            issues.append(
                QueryValidationIssue(
                    code="catalog_query_shape_not_allowed",
                    message="information_schema 仅允许单层 SELECT 查询",
                )
            )
            return self._result(None, issues, query_kind="catalog")

        # from_ 是 SQLGlot 对 FROM 子句使用的参数名；From.this 是 FROM 后的来源节点。
        # this 的含义随节点类型变化，并不总是字符串，这里可能是 Table 或 Subquery。
        from_expression = expression.args.get("from_")
        source = from_expression.this if from_expression is not None else None
        physical_tables = list(expression.find_all(exp.Table))
        # 当前过滤条件证明只覆盖单表直接 SELECT；更复杂的目录查询无法可靠证明
        # table_schema 约束作用于所有分支，因此直接拒绝。
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

        # named_selects 提供输出名称，例如 SELECT id AS order_id 得到 order_id。
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
        """确认系统目录查询通过 AND 条件限定到当前数据库。"""
        where = expression.args.get("where")
        if where is None:
            return False

        def terms(condition: Expr) -> list[Expr]:
            """展开括号和 AND，保留不能安全拆分的原子过滤条件。"""
            # A AND B 中 A 必须成立；A OR B 中 A 不一定成立，所以不能从 OR 内部
            # 提取 table_schema 条件来证明整个查询被限制在当前数据库。
            if isinstance(condition, exp.Paren):
                return terms(condition.this)
            if isinstance(condition, exp.And):
                return terms(condition.this) + terms(condition.expression)
            return [condition]

        # Where.this 是过滤表达式，terms 已递归移除各个合取项的外层括号。
        for condition in terms(where.this):
            # EQ 表示等号比较；LIKE、IN、!= 等不作为当前库范围的证明。
            if not isinstance(condition, exp.EQ):
                continue
            # 同时接受 table_schema = 'ecommerce' 和 'ecommerce' = table_schema。
            # 二元表达式的 this 和 expression 分别表示左、右操作数。
            for column, value in (
                (condition.this, condition.expression),
                (condition.expression, condition.this),
            ):
                if not (
                    isinstance(column, exp.Column)
                    and column.name.casefold() == "table_schema"
                ):
                    continue
                # Doris 的 DATABASE() 在 SQLGlot 中对应 CurrentSchema 节点。
                if isinstance(value, exp.CurrentSchema):
                    return True
                # Literal 是字面量；is_string 排除数值常量，this 取得不含 SQL 引号的值。
                if (
                    isinstance(value, exp.Literal)
                    and value.is_string
                    and value.this.casefold() == self._current_database.casefold()
                ):
                    return True
        return False

    @staticmethod
    def _check_readonly(expression: Expr) -> list[QueryValidationIssue]:
        """检查语句类型、危险节点和有副作用的函数。"""
        issues: list[QueryValidationIssue] = []
        # Query 是查询类节点的基类；find 返回找到的第一个 Select，没有则返回 None。
        # 同时检查两项，允许含 SELECT 的查询结构，但排除 INSERT ... SELECT 等写语句。
        if not isinstance(expression, exp.Query) or expression.find(exp.Select) is None:
            issues.append(
                QueryValidationIssue(
                    code="readonly_query_required",
                    message="仅允许执行 SELECT 或 WITH 只读查询语句",
                )
            )
            return issues
        # 顶层是查询还不够，嵌套表达式也必须排除禁止节点。
        # walk 遍历全部节点，node.key 是类型名称（如 insert）；集合推导式先去重。
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
        # 集合的 & 表示交集：找出本次 SQL 中实际使用的危险函数。
        forbidden_functions = sorted(anonymous_functions & _SIDE_EFFECT_FUNCTIONS)
        if forbidden_functions:
            issues.append(
                QueryValidationIssue(
                    code="forbidden_function",
                    message=("查询包含禁止的函数: " + ", ".join(forbidden_functions)),
                )
            )
        # 已报告为危险函数的名称不再重复报告为“未经批准”。
        # 集合的 - 表示差集：去掉危险函数和允许函数后，剩余名称均未获批准。
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
        """读取本次校验的表、字段目录，并在提供策略时按用户授权收窄。"""
        table_infos = await self._catalog_repo.list_table_infos()
        column_infos = await self._catalog_repo.list_column_infos()
        restricted_star_tables: frozenset[str] = frozenset()
        if policy is not None:
            authorization_filter = MetadataAuthorizationFilter(
                policy,
                self._data_source,
                self._current_database,
            )
            # 只有部分字段权限的表仍可能可见；过滤表时需要结合可见字段集合。
            allowed_column_keys = authorization_filter.allowed_column_keys(column_infos)
            table_infos = authorization_filter.filter_tables(
                table_infos,
                allowed_column_keys,
            )
            visible_table_names = {table.name for table in table_infos}
            # 必须在删除不可见字段之前做标记，否则无法区分“完整表”和“字段子集”。
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
        # 查找键忽略大小写，返回的资产名称仍以元数据原名为准。
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
        """字段级授权不允许通过星号扩展隐藏字段。"""
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
        """区分物理表与 CTE 并解析星号涉及的物理表。"""
        table_refs: dict[str, QueryTableRef] = {}
        star_tables: set[str] = set()
        issues: list[QueryValidationIssue] = []
        # 每层作用域分别解析别名，避免把 WITH orders AS (...) 中的 orders 当成物理表。
        for scope in traverse_scope(expression):
            physical_sources = self._physical_sources(scope, catalog, issues)
            for table_ref in physical_sources.values():
                table_refs[table_ref.qualified_name.casefold()] = table_ref
            if isinstance(scope.expression, exp.Query):
                for select in scope.expression.selects:
                    for star in select.find_all(exp.Star):
                        parent = star.parent
                        # t.* 只标记 t；未限定的 * 标记该作用域的全部直接物理来源。
                        # 当前实现也保守地统计 COUNT(*) 等表达式中的 Star 节点。
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
        """解析作用域别名对应的物理表。"""
        sources: dict[str, QueryTableRef] = {}
        # selected_sources 将当前作用域的别名映射到 (引用节点, 实际来源)；
        # 这里用 _ 忽略引用节点，只使用 source 区分物理表和派生查询。
        for alias, (_, source) in scope.selected_sources.items():
            # CTE/派生表的 source 是 Scope；其内部物理表会在遍历对应作用域时处理。
            if not isinstance(source, exp.Table):
                continue
            # 对 catalog.db.table：catalog、db、name 分别取三段名称；
            # 未写数据库时 source.db 为空，因此用 or 回退到当前业务库。
            catalog_name = source.catalog
            database = source.db or self._current_database
            table_key = source.name.casefold()
            table_name = catalog.table_names.get(table_key, source.name)
            table_ref = QueryTableRef(database=database, name=table_name)
            # 首轮表解析传入 issues 并报告错误；后续收集字段复用映射，不重复报错。
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
        """基于元数据补全并验证字段、别名和 CTE 引用。"""
        schema = catalog.sqlglot_schema
        if self._current_database:
            schema = {self._current_database: schema}
        # qualify 会改写 AST，因此复制原树，保留原始引用用于错误归类。
        # cast 只向类型检查器声明类型，不会在运行时转换或验证对象。
        return cast(
            exp.Query,
            qualify(
                expression.copy(),
                dialect="doris",
                db=self._current_database,
                schema=cast(dict[str, object], schema),
                # 展开输出别名与星号，使后续检查能看到实际字段及其所属来源。
                expand_alias_refs=True,
                expand_stars=True,
                # 禁止猜测缺失的表结构，字段必须能通过传入的可见目录解析。
                infer_schema=False,
                validate_qualify_columns=True,
                # 不要求给所有标识符统一加引号，最终仍按 Doris 方言生成 SQL。
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
        """把 sqlglot 字段解析错误转换为稳定错误码。"""
        message = str(error)
        # 优化器异常没有统一的结构化字段名，这里尝试提取；提取失败仍返回引用错误。
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
        """判断未限定字段是否同时存在于多个当前作用域来源。"""
        column_key = column_name.casefold()
        for scope in traverse_scope(expression):
            # 只有未带表名前缀的列需要歧义判断，例如两张表都有 id 时直接 SELECT id。
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
                # 派生表/CTE 对外暴露的是输出列名，不是内部物理表的全部字段。
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
        """收集字段血缘中直接引用的物理字段。"""
        references: dict[str, QueryColumnRef] = {}
        # 在 qualify 之后执行：字段已有来源信息，星号也已展开。
        # scope.columns 不只包含输出列，还包含 WHERE、JOIN 等位置的引用。
        for scope in traverse_scope(expression):
            physical_sources = self._physical_sources(scope, catalog)
            for column in scope.columns:
                if not column.table:
                    continue
                table_ref = physical_sources.get(column.table.casefold())
                if table_ref is None:
                    # 当前别名可能指向 CTE/派生表；其物理字段由对应内部作用域收集。
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
                # 同一字段可在多个表达式中使用，资产依赖只需保留一次。
                references[reference.qualified_name.casefold()] = reference
        return sorted(
            references.values(),
            key=lambda column: column.qualified_name.casefold(),
        )

    @classmethod
    def _check_joins(cls, expression: Expr) -> list[QueryValidationIssue]:
        """检查 JOIN 的来源关联结构，拒绝明显缺失连接关系的写法。"""
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
                # 每处理一个 JOIN 就把右表加入左侧集合，后续连接必须关联已经形成的
                # 数据源集合，不能只引用自身或无关别名。
                # Join.this 是右侧来源；alias_or_name 优先取别名，没有别名才取来源名。
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
                # USING 是显式关联条件；本阶段在 qualify 之后检查，需兼容其展开结果。
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
                left_aliases.add(right_alias)
        return issues

    @classmethod
    def _join_condition_links_sources(
        cls,
        condition: Expr,
        left_aliases: set[str],
        right_alias: str,
    ) -> bool:
        """确认 JOIN 条件的布尔分支包含跨来源比较。"""
        if isinstance(condition, (exp.Paren, exp.Not)):
            # 当前规则只检查比较是否跨来源，NOT 不改变这里对来源的归类。
            return cls._join_condition_links_sources(
                condition.this,
                left_aliases,
                right_alias,
            )
        if isinstance(condition, (exp.Or, exp.Xor)):
            # 每个分支都要有关联条件，避免 a.id = b.id OR b.enabled = 1 这样的
            # 独立右表条件绕开关联检查。这里只检查结构，不推导布尔表达式的等价形式。
            return all(
                cls._join_condition_links_sources(
                    child,
                    left_aliases,
                    right_alias,
                )
                for child in (condition.this, condition.expression)
            )
        if isinstance(condition, exp.And):
            # 合取条件有一个跨来源比较即可，例如 a.id = b.id AND b.enabled = 1。
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
        # 对其他包装表达式递归寻找符合上述规则的子表达式，不做完整逻辑求解。
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
        """判断比较操作的两侧分别只引用前置来源和当前右侧来源。"""

        def source_side(operand: Expr) -> str | None:
            """判断一个比较操作数仅引用 Join 的哪一侧来源。"""
            aliases = {
                column.table.casefold()
                for column in operand.find_all(exp.Column)
                if column.table
            }
            # 允许操作数含表达式，如 a.id + 1；但其所有列必须来自同一侧。
            # 纯常量、混合左右列或无关别名都返回 None，不能单独证明两侧有关联。
            # 集合的 <= 表示子集；== {right_alias} 要求只引用当前右侧来源。
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

    def _check_asset_policy(
        self,
        policy: AssetAccessPolicy,
        tables: list[QueryTableRef],
        columns: list[QueryColumnRef],
        star_tables: set[str],
    ) -> list[QueryValidationIssue]:
        """逐个检查星号表和显式物理字段的资产权限。"""
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
                # 星号和未解析出显式字段的访问需要表级授权；显式列随后逐列校验。
                # 例如 SELECT 1 FROM orders 虽不读取具体字段，仍然访问了表中的行。
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
                    # 整表访问已经按表级权限判定，无需再为展开的每一列重复报告问题。
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
        """返回忽略大小写后的重复输出名。"""
        seen: set[str] = set()
        duplicates: dict[str, str] = {}
        # id 与 ID 视为同名；报告保留首次发现重复时的拼写，并按忽略大小写排序。
        for name in names:
            key = name.casefold()
            if key in seen:
                duplicates.setdefault(key, name)
            seen.add(key)
        return sorted(duplicates.values(), key=str.casefold)
