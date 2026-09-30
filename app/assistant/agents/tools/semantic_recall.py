"""Explorer 召回工具：参数校验、用例调用和结果投影。"""

from typing import Any, cast
from uuid import UUID

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from loguru import logger

from app.assistant.agents.tools.errors import tool_error
from app.metadata.errors import SemanticQueriesNotFoundError, SemanticRecallSaveError
from app.metadata.models.recall import (
    DeleteRecallsRequest,
    GetRecallRequest,
    ListRecallsRequest,
    MergeRecallsRequest,
    RecallContextRequest,
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
    SemanticRecallUpdate,
)
from app.metadata.models.search import (
    SemanticResourceRecallRequest,
    SemanticResourceType,
)
from app.metadata.services.recall_application import SemanticRecallService


def _recall_identity(config: RunnableConfig) -> tuple[int, UUID]:
    """读取服务端注入的会话身份。"""
    configurable = cast(dict[str, Any], config)["configurable"]
    return configurable["user_id"], UUID(configurable["conversation_id"])


def _recall_error(
    message: str, error: Exception, *, missing_message: str = ""
) -> dict[str, Any]:
    """保留记录缺失与其他业务失败的不同响应。"""
    if missing_message and isinstance(error, SemanticQueriesNotFoundError):
        return {"status": "error", "message": missing_message, "queries": error.queries}
    logger.opt(exception=error).error(message)
    if isinstance(error, SemanticRecallSaveError):
        cause = error.__cause__
        return tool_error(
            "无法保存语义召回快照", cause if isinstance(cause, Exception) else error
        )
    return tool_error(message, error)


def create_semantic_recall_tools(recall: SemanticRecallService) -> list[BaseTool]:
    """请求模型由工具框架校验，业务编排由 metadata 承担。"""

    @tool(args_schema=RecallContextRequest)
    async def recall_context(
        config: RunnableConfig,
        query: str,
        resource_types: list[SemanticResourceType],
        terms: list[str],
        limit_per_type: int = 5,
    ) -> dict[str, Any]:
        """按 query 累计召回；首次返回全量，后续返回增量及本次召回数量。"""
        try:
            user_id, conversation_id = _recall_identity(config)
            record = await recall.recall_context(
                user_id,
                conversation_id,
                query,
                SemanticResourceRecallRequest.model_construct(
                    terms=terms,
                    resource_types=resource_types,
                    limit_per_type=limit_per_type,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return _recall_error("语义资源召回失败", exc)
        return semantic_recall_update(record)

    @tool(args_schema=ListRecallsRequest)
    async def list_recalls(config: RunnableConfig, limit: int = 20) -> dict[str, Any]:
        """列出当前会话中每个 query 的最新累计召回记录。"""
        try:
            user_id, conversation_id = _recall_identity(config)
            records = await recall.list_recalls(user_id, conversation_id, limit)
        except Exception as exc:  # noqa: BLE001
            return _recall_error("获取语义召回列表失败", exc)
        return {
            "status": "success",
            "recalls": [
                {
                    "query": record.query,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in records
            ],
        }

    @tool(args_schema=GetRecallRequest)
    async def get_recall(config: RunnableConfig, query: str) -> dict[str, Any]:
        """按 query 读取当前会话的最新累计召回记录。"""
        try:
            user_id, conversation_id = _recall_identity(config)
            record = await recall.get_recall(user_id, conversation_id, query)
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "加载语义召回记录失败", exc, missing_message="未找到指定的语义召回记录"
            )
        return semantic_recall_payload(record)

    @tool(args_schema=MergeRecallsRequest)
    async def merge_recalls(
        config: RunnableConfig, target_query: str, source_query: str
    ) -> dict[str, Any]:
        """合并来源 query 的语义资源并删除来源。"""
        try:
            user_id, conversation_id = _recall_identity(config)
            record = await recall.merge_recalls(
                user_id, conversation_id, target_query, source_query
            )
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "无法合并语义召回记录",
                exc,
                missing_message="未找到待合并的语义召回记录",
            )
        return semantic_recall_payload(record)

    @tool(args_schema=DeleteRecallsRequest)
    async def delete_recalls(
        config: RunnableConfig, deletions: list[SemanticRecallResourceDeletion]
    ) -> dict[str, Any]:
        """删除当前会话 query 的全部上下文或其中指定资源。"""
        try:
            user_id, conversation_id = _recall_identity(config)
            await recall.delete_recalls(user_id, conversation_id, deletions)
        except Exception as exc:  # noqa: BLE001
            return _recall_error(
                "无法删除语义召回记录",
                exc,
                missing_message="未找到待删除的语义召回记录",
            )
        return {
            "status": "success",
            "recalls": [
                {
                    "status": "deleted" if deletion.deletes_entire_query else "updated",
                    **deletion.model_dump(mode="json", exclude_defaults=True),
                }
                for deletion in deletions
            ],
        }

    return [recall_context, list_recalls, get_recall, merge_recalls, delete_recalls]


def semantic_recall_payload(
    record: SemanticRecallRecord,
) -> dict[str, Any]:
    """投影模型执行 SQL 所需的元数据和历史经验。"""
    response = record.response
    values_by_column: dict[tuple[str, str], list[str]] = {}
    for item in response.values:
        values_by_column.setdefault((item.t_name, item.c_name), []).append(item.value)

    tables: dict[str, dict[str, Any]] = {
        item.name: {
            "role": item.role,
            "description": item.description,
            "primary_key_columns": item.primary_key_columns,
            "columns": {},
        }
        for item in response.tables
    }
    for item in response.columns:
        table = tables.get(item.t_name)
        if table is None:
            continue
        column = item.model_dump(
            mode="json",
            include={
                "type",
                "description",
                "alias",
                "examples",
                "reference_t_name",
                "reference_c_name",
            },
        )
        values = values_by_column.get((item.t_name, item.name))
        if values:
            column["values"] = values
        table["columns"][item.name] = column

    return {
        "status": response.status,
        "mode": "full",
        "query": record.query,
        "failures": [item.model_dump(mode="json") for item in response.failures],
        "warnings": response.warnings,
        "truncated": response.truncated,
        "tables": tables,
        "metrics": {
            item.name: item.model_dump(
                mode="json", include={"description", "alias", "relevant_columns"}
            )
            for item in response.metrics
        },
        "query_experiences": [
            experience.model_dump(
                mode="json",
                include={
                    "id": True,
                    "purpose": True,
                    "sql_template": True,
                    "assets": {"__all__": {"kind", "database", "table", "column"}},
                },
            )
            for experience in record.query_experiences
        ],
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def semantic_recall_update(update: SemanticRecallUpdate) -> dict[str, Any]:
    """首次返回全量，后续只返回变化；数量始终来自本次检索而非累计快照。"""
    payload = semantic_recall_payload(update.record)
    recalled = update.recalled
    payload.update(
        status=recalled.status,
        recalled_counts={
            "tables": len(recalled.tables),
            "columns": len(recalled.columns),
            "values": len(recalled.values),
            "metrics": len(recalled.metrics),
            "query_experiences": len(update.record.query_experiences),
        },
        failures=[item.model_dump(mode="json") for item in recalled.failures],
        warnings=recalled.warnings,
        truncated=recalled.truncated,
    )
    if update.previous is None:
        return payload

    previous = semantic_recall_payload(update.previous)
    changed_tables: dict[str, Any] = {}
    removed_tables: dict[str, Any] = {}
    for name, table in payload["tables"].items():
        old_table = previous["tables"].get(name)
        if old_table is None:
            changed_tables[name] = table
            continue
        changed = {
            key: value
            for key, value in table.items()
            if key != "columns" and value != old_table.get(key)
        }
        columns = {}
        removed_columns = {}
        for column_name, column in table["columns"].items():
            old_column = old_table["columns"].get(column_name)
            if old_column is None:
                columns[column_name] = column
                continue
            column_changes = {
                key: value
                for key, value in column.items()
                if key != "values" and value != old_column.get(key)
            }
            old_values = old_column.get("values", [])
            values = column.get("values", [])
            added_values = [value for value in values if value not in old_values]
            removed_values = [value for value in old_values if value not in values]
            if added_values:
                column_changes["values"] = added_values
            if removed_values:
                removed_columns[column_name] = {"values": removed_values}
            if column_changes:
                columns[column_name] = column_changes
        for column_name in sorted(
            old_table["columns"].keys() - table["columns"].keys()
        ):
            removed_columns[column_name] = {}
        if columns:
            changed["columns"] = columns
        if changed:
            changed_tables[name] = changed
        if removed_columns:
            removed_tables[name] = {"columns": removed_columns}
    for name in sorted(previous["tables"].keys() - payload["tables"].keys()):
        removed_tables[name] = {}

    old_experiences = {item["id"]: item for item in previous["query_experiences"]}
    experiences = {item["id"]: item for item in payload["query_experiences"]}
    removed = {
        "tables": removed_tables,
        "metrics": sorted(previous["metrics"].keys() - payload["metrics"].keys()),
        "query_experiences": sorted(old_experiences.keys() - experiences.keys()),
    }
    payload.update(
        mode="delta",
        tables=changed_tables,
        metrics={
            name: item
            for name, item in payload["metrics"].items()
            if item != previous["metrics"].get(name)
        },
        query_experiences=[
            item
            for key, item in experiences.items()
            if item != old_experiences.get(key)
        ],
    )
    if any(removed.values()):
        payload["removed"] = removed
    return payload
