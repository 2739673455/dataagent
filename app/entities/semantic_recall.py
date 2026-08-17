"""语义召回记录模型"""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.entities.semantic_search import SemanticSearchRequest, SemanticSearchResponse

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
            raise ValueError("response.search_id must equal recall_id")
        if self.kind == "search":
            if self.request is None:
                raise ValueError("search recall requires request")
            if self.source_recall_ids:
                raise ValueError("search recall cannot have source recalls")
        else:
            if self.request is not None:
                raise ValueError("merged recall cannot have request")
            if len(self.source_recall_ids) < 2:
                raise ValueError("merged recall requires at least two sources")
        return self
