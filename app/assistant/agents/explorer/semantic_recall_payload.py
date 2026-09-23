"""语义召回工具结果投影。"""

from typing import Any

from app.metadata.models.search import SemanticResourceRecallResponse


def semantic_recall_payload(
    response: SemanticResourceRecallResponse,
) -> dict[str, Any]:
    """投影模型执行 SQL 所需的元数据。"""
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
        "tables": tables,
        "metrics": {
            item.name: {
                "description": item.description,
                "alias": item.alias,
                "relevant_columns": item.relevant_columns,
            }
            for item in response.metrics
        },
    }
