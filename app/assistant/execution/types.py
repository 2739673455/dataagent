"""Dynamic Subagents 的公共协议。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from app.sandbox import (
    conversation_workspace_path,
)
from app.shared.contracts.analysis import IDENTIFIER_PATTERN, AgentType

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from app.assistant.execution.session_service import AgentSessionService
    from app.assistant.execution.shell_jobs import ShellJobRuntime

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


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID。"""
    if isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("user_id 必须为正整数")
    return f"user_{user_id}:conversation_{conversation_id}"


def conversation_lifecycle_lock_name(user_id: int, conversation_id: UUID) -> str:
    """构造跨进程会话生命周期锁名称。"""
    return f"conversation:{get_thread_id(user_id, conversation_id)}"


def build_planner_config(user_id: int, conversation_id: UUID) -> RunnableConfig:
    """创建 Planner 根 namespace 的运行配置。"""
    return RunnableConfig(
        configurable={
            "thread_id": get_thread_id(user_id, conversation_id),
            "checkpoint_ns": "",
            "user_id": user_id,
            "conversation_id": str(conversation_id),
            "workspace_dir": conversation_workspace_path(conversation_id),
        }
    )


@dataclass(frozen=True, slots=True)
class PlannerTurnContext:
    """Turn 是处理一次用户输入的逻辑回合，可包含多次模型续写。

    中断后恢复仍处理同一回合；Run 则是承载执行与订阅的进程内实例。"""

    user_id: int
    conversation_id: UUID
    max_continuations: int

    def __post_init__(self) -> None:
        """校验 Planner 回合上下文中的身份和续写参数。"""
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
        if self.max_continuations < 0:
            raise ValueError("max_continuations 不能为负数")


type SubagentRunStatus = Literal[
    "running",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class SubagentMessageActivity:
    """一次 Specialist 执行产生的公开候选消息。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class SubagentThinkingDeltaActivity:
    """一次 Specialist 模型调用产生的思考增量。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str
    delta: str
    reset: bool = False


@dataclass(frozen=True, slots=True)
class SubagentMessageDeltaActivity:
    """一次 Specialist 模型调用产生的正文增量。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message_id: str
    delta: str
    reset: bool = False


@dataclass(frozen=True, slots=True)
class SubagentStatusActivity:
    """一次 Specialist 执行的状态变化。"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    status: SubagentRunStatus


type SubagentActivity = (
    SubagentMessageActivity
    | SubagentThinkingDeltaActivity
    | SubagentMessageDeltaActivity
    | SubagentStatusActivity
)
type SubagentActivityWriter = Callable[[SubagentActivity], None]


@dataclass(slots=True)
class ConversationAgentRuntime:
    """一个用户会话内的 Agent 运行时资源。"""

    planner: CompiledStateGraph
    session_service: AgentSessionService
    shell_jobs: ShellJobRuntime


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的协议模型基类。"""

    model_config = ConfigDict(extra="forbid", strict=True)


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


@dataclass(frozen=True, slots=True)
class DelegationActivityHistory:
    """一次 delegation 的公开消息和真实执行状态。"""

    messages: list[BaseMessage]
    status: SubagentRunStatus


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
