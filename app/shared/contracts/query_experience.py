"""查询经验检索结果契约"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

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

    purpose: str
    sql_template: str
    dialect: str
    assets: list[QueryAssetSnapshot]
