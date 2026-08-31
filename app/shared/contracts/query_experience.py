"""查询经验检索结果契约。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

type QueryAssetKind = Literal["table", "column"]
QUERY_EXPERIENCE_RECALL_LIMIT = 3
QueryExperienceRecallStatus = Literal["success", "partial", "failed"]


class QueryAssetSnapshot(BaseModel):
    """查询经验返回的资产引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QueryAssetKind
    database: str
    table: str
    column: str | None = None
    meta_version: int


class QueryExperienceRecallResult(BaseModel):
    """提供给 Explorer 的紧凑查询经验。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    purpose: str
    sql_template: str
    assets: list[QueryAssetSnapshot]


class QueryExperienceRecall(BaseModel):
    """一次查询经验召回的结果及检索通道状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: QueryExperienceRecallStatus
    results: list[QueryExperienceRecallResult]
