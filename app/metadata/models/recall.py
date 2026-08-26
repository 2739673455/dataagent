"""语义召回记录模型"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.metadata.models.search import SemanticSearchRequest, SemanticSearchResponse

SemanticRecallKind = Literal["search", "merged"]


class SemanticRecallRecord(BaseModel):
    """一次独立检索或多次检索的合并快照"""

    model_config = ConfigDict(extra="forbid")

    recall_id: str
    user_id: int
    conversation_id: UUID
    kind: SemanticRecallKind
    request: SemanticSearchRequest | None
    response: SemanticSearchResponse
    source_recall_ids: list[str]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_kind_payload(self) -> Self:
        """校验原始召回和合并召回的数据约束"""
        if self.response.search_id != self.recall_id:
            raise ValueError("response.search_id 必须与 recall_id 一致")
        if self.kind == "search":
            if self.request is None:
                raise ValueError("原始检索召回必须包含 request")
            if self.source_recall_ids:
                raise ValueError("原始检索召回不能包含来源召回")
        else:
            if self.request is not None:
                raise ValueError("合并召回不能包含 request")
            if len(self.source_recall_ids) < 2:
                raise ValueError("合并召回至少需要两个来源召回")
        return self
