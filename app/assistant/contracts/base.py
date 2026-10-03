"""Assistant 共用标识、协议基类与 Planner 回合上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
)

from app.shared.contracts.analysis import IDENTIFIER_PATTERN

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=IDENTIFIER_PATTERN.pattern,
    ),
]

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

MESSAGE_CREATED_AT_KEY = "dataagent_created_at"

DELEGATION_CONTEXT_KEY = "dataagent_delegation_context"


@dataclass(frozen=True, slots=True)
class PlannerTurnContext:
    """Turn 是处理一次用户输入的逻辑回合，可包含多次模型续写。

    中断后恢复仍处理同一回合；Run 则是承载执行与订阅的进程内实例。"""

    user_id: int
    conversation_id: UUID
    max_continuations: int


type SubagentRunStatus = Literal[
    "running",
    "completed",
    "failed",
    "cancelled",
]


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的协议模型基类。"""

    model_config = ConfigDict(extra="forbid", strict=True)
