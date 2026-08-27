"""查询经验检索结果契约"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

type QueryExperienceQuality = Literal["candidate", "promoted", "disabled"]
type QueryAssetKind = Literal["table", "column"]


class QueryAssetSnapshot(BaseModel):
    """查询经验返回的资产引用"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QueryAssetKind
    database: str
    table: str
    column: str | None = None
    meta_version: int


class QueryExperienceSearchResult(BaseModel):
    """提供给 Explorer 的紧凑查询经验"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experience_id: UUID
    purpose: str
    sql_template: str
    dialect: str
    assets: list[QueryAssetSnapshot]
    quality: QueryExperienceQuality
    success_count: int
    adopted_count: int
    score: float
    match_reasons: list[str]
    last_used_at: datetime
