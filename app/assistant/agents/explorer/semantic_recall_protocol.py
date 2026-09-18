"""Explorer 语义召回的持久化消息协议。"""

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolMessage

from app.metadata.models.recall import (
    SemanticRecallRecord,
    SemanticRecallResourceDeletion,
)

_REFERENCE_TOOLS = frozenset(
    {"recall_context", "get_recall", "merge_recalls", "delete_recalls"}
)


@dataclass(frozen=True, slots=True)
class SemanticRecallReference:
    """描述一条待展开的召回引用或整条删除确认。"""

    query: str
    deleted: bool = False


def semantic_recall_reference(
    record: SemanticRecallRecord,
) -> dict[str, Any]:
    """构造只含持久化记录引用的工具结果。"""
    return {
        "status": "stored",
        "query": record.query,
    }


def semantic_recall_deletion_result(
    deletions: list[SemanticRecallResourceDeletion],
) -> dict[str, Any]:
    """持久化局部删除后的引用或整条删除确认，不嵌入资产内容。"""
    return {
        "status": "success",
        "recalls": [
            {
                "status": "deleted" if deletion.deletes_entire_query else "stored",
                "query": deletion.query,
            }
            for deletion in deletions
        ],
    }


def parse_semantic_recall_references(
    message: ToolMessage,
) -> tuple[SemanticRecallReference, ...] | None:
    """解析单条召回引用或有序的批量删除结果。"""
    if message.name not in _REFERENCE_TOOLS or not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    batch = message.name == "delete_recalls"
    if batch:
        if payload.get("status") != "success" or not isinstance(
            payload.get("recalls"), list
        ):
            return None
        items = payload["recalls"]
    else:
        items = [payload]
    if not items:
        return None
    references: list[SemanticRecallReference] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return None
        status = item.get("status")
        if status != "stored" and not (batch and status == "deleted"):
            return None
        query = item.get("query")
        if (
            not isinstance(query, str)
            or not query
            or query != query.strip()
            or query in seen
        ):
            return None
        seen.add(query)
        references.append(
            SemanticRecallReference(query=query, deleted=status == "deleted")
        )
    return tuple(references)


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
        column = {
            "type": item.type,
            "description": item.description,
            "alias": item.alias,
            "examples": item.examples,
            "reference_t_name": item.reference_t_name,
            "reference_c_name": item.reference_c_name,
        }
        values = values_by_column.get((item.t_name, item.name))
        if values:
            column["values"] = values
        table["columns"][item.name] = column

    return {
        "query": record.query,
        "tables": tables,
        "metrics": {
            item.name: {
                "description": item.description,
                "alias": item.alias,
                "relevant_columns": item.relevant_columns,
            }
            for item in response.metrics
        },
        "query_experiences": [
            {
                "id": str(experience.id),
                "purpose": experience.purpose,
                "sql_template": experience.sql_template,
                "assets": [
                    {
                        "kind": asset.kind,
                        "database": asset.database,
                        "table": asset.table,
                        "column": asset.column,
                    }
                    for asset in experience.assets
                ],
            }
            for experience in record.query_experiences
        ],
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
