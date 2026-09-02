"""查询执行审计与成功经验聚合。"""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlglot import exp, parse_one

from app.query.models.execution import QueryExecution, QueryExecutionStatus
from app.query.models.experience import QueryExperience, QueryExperienceAsset
from app.query.models.validation import QueryValidationResult
from app.query.repositories.execution_postgres import QueryExecutionPGRepo
from app.query.repositories.experience_postgres import QueryExperiencePGRepo
from app.query.services.contracts import QueryExperienceIndexScheduler
from app.query.services.executor import SuccessfulQueryExecution
from app.shared.contracts.analysis import AgentSessionKey
from app.shared.contracts.assets import asset_resource_key


@dataclass(frozen=True, slots=True)
class QueryExecutionContext:
    """SQL 工具提供的用户、角色和任务上下文。"""

    session_key: AgentSessionKey
    role_name: str
    authorization_epoch: UUID
    purpose: str
    tool_call_id: str | None = None


def _build_sql_template(sql: str) -> tuple[str, str]:
    """将 SQL 字面量替换为参数并生成稳定结构指纹。"""
    expression = parse_one(sql, read="doris")
    parameter_index = 0
    for node in list(expression.walk()):
        if not isinstance(node, exp.Literal):
            continue
        parameter_index += 1
        node.replace(exp.Placeholder(this=f"p{parameter_index}"))
    template = expression.sql(dialect="doris", pretty=False)
    fingerprint = hashlib.sha256(template.encode()).hexdigest()
    return template, fingerprint


class QueryExecutionRecorder:
    """记录查询执行并聚合成功的业务查询经验。"""

    def __init__(
        self,
        execution_repo: QueryExecutionPGRepo,
        experience_repo: QueryExperiencePGRepo,
        index_scheduler: QueryExperienceIndexScheduler,
        *,
        data_source: str,
        database_name: str,
    ) -> None:
        """绑定执行记录、经验存储和索引调度依赖。"""
        self._execution_repo = execution_repo
        self._experience_repo = experience_repo
        self._index_scheduler = index_scheduler
        self._data_source = data_source
        self._database_name = database_name

    async def record_success(
        self,
        context: QueryExecutionContext,
        details: SuccessfulQueryExecution,
    ) -> UUID | None:
        """记录成功执行并增量更新相同结构的查询经验。"""
        execution = self._new_execution(context, details.raw_sql, "succeeded")
        execution.normalized_sql = details.normalized_sql
        execution.validation = details.validation.model_dump(mode="json")
        execution.result_summary = self._result_summary(details)
        if details.validation.query_kind == "catalog":
            async with self._experience_repo.session.begin():
                await self._execution_repo.record(execution)
            return None

        sql_template, fingerprint = _build_sql_template(details.normalized_sql)
        execution.sql_template = sql_template
        execution.fingerprint = fingerprint
        tables = {item.name for item in details.validation.tables}
        columns = {(item.table, item.name) for item in details.validation.columns}
        async with self._experience_repo.session.begin():
            (
                table_versions,
                column_versions,
            ) = await self._experience_repo.metadata_versions(tables, columns)
            experience_id = uuid4()
            experience = QueryExperience(
                id=experience_id,
                role_name=context.role_name,
                authorization_epoch=context.authorization_epoch,
                fingerprint=fingerprint,
                purposes=[context.purpose],
                sql_template=sql_template,
            )
            assets = self._build_assets(
                experience_id,
                details.validation,
                table_versions,
                column_versions,
            )
            stored = await self._experience_repo.upsert_from_success(
                experience,
                assets,
            )
            execution.experience_id = stored.id
            await self._execution_repo.record(execution)
        self._index_scheduler.enqueue(stored.id, stored.revision)
        return stored.id

    async def record_failure(
        self,
        context: QueryExecutionContext,
        *,
        raw_sql: str,
        status: QueryExecutionStatus,
        error_code: str,
        error_detail: str,
        validation: QueryValidationResult | None = None,
    ) -> None:
        """记录被 Guard 拒绝或执行失败的 SQL。"""
        execution = self._new_execution(context, raw_sql, status)
        execution.error_code = error_code
        execution.error_detail = error_detail[:4000]
        if validation is not None:
            execution.normalized_sql = validation.normalized_sql
            execution.validation = validation.model_dump(mode="json")
        async with self._experience_repo.session.begin():
            await self._execution_repo.record(execution)

    @staticmethod
    def _new_execution(
        context: QueryExecutionContext,
        raw_sql: str,
        status: QueryExecutionStatus,
    ) -> QueryExecution:
        """构造三个记录分支共用的执行上下文字段。"""
        return QueryExecution(
            user_id=context.session_key.user_id,
            role_name=context.role_name,
            authorization_epoch=context.authorization_epoch,
            conversation_id=context.session_key.conversation_id,
            analysis_id=context.session_key.analysis_id,
            session_id=context.session_key.session_id,
            tool_call_id=context.tool_call_id,
            purpose=context.purpose,
            raw_sql=raw_sql,
            status=status,
        )

    @staticmethod
    def _result_summary(details: SuccessfulQueryExecution) -> dict[str, object]:
        """构造成功执行的持久化结果摘要。"""
        return {
            "path": details.result.path,
            "columns": [
                item.model_dump(mode="json") for item in details.result.columns
            ],
            "row_count": details.result.row_count,
            "time_range": {
                key: value.model_dump(mode="json")
                for key, value in details.result.time_range.items()
            },
        }

    def _build_assets(
        self,
        experience_id: UUID,
        validation: QueryValidationResult,
        table_versions: dict[str, int],
        column_versions: dict[tuple[str, str], int],
    ) -> list[QueryExperienceAsset]:
        """按校验血缘构造带元数据版本的经验资产快照。"""
        assets = [
            QueryExperienceAsset(
                experience_id=experience_id,
                kind="table",
                resource_key=asset_resource_key(
                    self._data_source,
                    table.database or self._database_name,
                    table.name,
                ),
                data_source=self._data_source,
                database_name=table.database or self._database_name,
                table_name=table.name,
                column_name=None,
                meta_version=table_versions.get(table.name, 0),
            )
            for table in validation.tables
        ]
        assets.extend(
            QueryExperienceAsset(
                experience_id=experience_id,
                kind="column",
                resource_key=asset_resource_key(
                    self._data_source,
                    column.database or self._database_name,
                    column.table,
                    column.name,
                ),
                data_source=self._data_source,
                database_name=column.database or self._database_name,
                table_name=column.table,
                column_name=column.name,
                meta_version=column_versions.get((column.table, column.name), 0),
            )
            for column in validation.columns
        )
        return assets
