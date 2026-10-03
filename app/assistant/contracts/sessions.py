"""专家 Session 管理、委派请求、结果与持久化状态协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    Field,
    StringConstraints,
)

from app.assistant.contracts.base import (
    Identifier,
    NonEmptyText,
    StrictProtocolModel,
    SubagentRunStatus,
)
from app.shared.contracts.analysis import AgentType


class DelegationMessageContext(StrictProtocolModel):
    """持久化在 Specialist 输入消息中的委派边界。"""

    delegation_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    ]


class DelegationRequest(StrictProtocolModel):
    """Delegation 是 Planner 向专业 Agent 发起的一次工作委派。

    请求定位可复用的 Session；每次委派以独立 delegation_id 记录结果，
    同一 Session 可以接收多次委派并延续工作上下文。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    message: NonEmptyText


class ListSessionsRequest(StrictProtocolModel):
    """查询当前 Conversation 内专业 Session 的请求。"""

    analysis_id: Identifier | None = None


class DeleteSessionRequest(StrictProtocolModel):
    """删除专业 Agent Session 的请求。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier


class DelegationResult(StrictProtocolModel):
    """运行时记录的委派状态、Session 身份和 Agent 原始文本。"""

    status: Literal["completed", "failed"]
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    content: str = Field(min_length=1)


class DelegationCheckpointRecord(StrictProtocolModel):
    """持久化一次委派的运行状态和文本结果。"""

    delegation_id: NonEmptyText
    status: SubagentRunStatus
    result: DelegationResult | None = None


class SessionSummary(StrictProtocolModel):
    """单个专业 Agent Session 的结构化摘要。"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    status: Literal[
        "active",
        "completed",
        "failed",
        "interrupted",
    ]
    summary: NonEmptyText | None = None
    updated_at: datetime | None = None


class ListSessionsResult(StrictProtocolModel):
    """当前 Conversation 内的专业 Session 列表。"""

    analysis_id: Identifier | None = None
    sessions: list[SessionSummary]


class DeleteSessionResult(StrictProtocolModel):
    """删除专业 Agent Session 的成功响应。"""

    status: Literal["success"] = "success"
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    existed: bool
    message: NonEmptyText
