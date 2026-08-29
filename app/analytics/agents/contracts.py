"""Dynamic Subagents 的公共协议"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal, Self
from uuid import UUID

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.shared.contracts.analysis import IDENTIFIER_PATTERN, AgentType

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from app.analytics.agents.session_service import AgentSessionService

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
    """构造全局唯一的 LangGraph 会话线程 ID"""
    if isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("user_id 必须为正整数")
    return f"user_{user_id}:conversation_{conversation_id}"


def conversation_lifecycle_lock_name(user_id: int, conversation_id: UUID) -> str:
    """构造跨进程会话生命周期锁名称"""
    return f"conversation:{get_thread_id(user_id, conversation_id)}"


def build_planner_config(user_id: int, conversation_id: UUID) -> RunnableConfig:
    """创建 Planner 根 namespace 的运行配置"""
    return RunnableConfig(
        configurable={
            "thread_id": get_thread_id(user_id, conversation_id),
            "checkpoint_ns": "",
            "user_id": user_id,
            "conversation_id": str(conversation_id),
            "workspace_dir": "/",
        }
    )


@dataclass(frozen=True, slots=True)
class PlannerTurnContext:
    """绑定一个用户回合的身份和续写上限"""

    user_id: int
    conversation_id: UUID
    max_continuations: int

    def __post_init__(self) -> None:
        """校验 Planner 回合上下文中的身份和续写参数"""
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
        if self.max_continuations < 0:
            raise ValueError("max_continuations 不能为负数")


type SubagentRunStatus = Literal[
    "running",
    "completed",
    "needs_repair",
    "failed",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class SubagentMessageActivity:
    """一次 Specialist 执行产生的公开候选消息"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    message: BaseMessage


@dataclass(frozen=True, slots=True)
class SubagentStatusActivity:
    """一次 Specialist 执行的状态变化"""

    delegation_id: str
    analysis_id: str
    agent_type: AgentType
    session_id: str
    status: SubagentRunStatus


type SubagentActivity = SubagentMessageActivity | SubagentStatusActivity
type SubagentActivityWriter = Callable[[SubagentActivity], None]


@dataclass(slots=True)
class ConversationAgentRuntime:
    """一个用户会话内的 Agent 运行时资源"""

    planner: CompiledStateGraph
    session_service: AgentSessionService
    planner_lock: Callable[[], AbstractAsyncContextManager[None]]
    conversation_deleted: Callable[[], Awaitable[bool]]


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的协议模型基类"""

    model_config = ConfigDict(extra="forbid", strict=True)


class DelegationMessageContext(StrictProtocolModel):
    """持久化在 Specialist 输入消息中的委派边界"""

    delegation_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    ]


class DelegationRequest(StrictProtocolModel):
    """Planner 发起专业 Agent 委派的请求"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    message: NonEmptyText


class ListSessionsRequest(StrictProtocolModel):
    """查询当前 Conversation 内专业 Session 的请求"""

    analysis_id: Identifier | None = None


class DeleteSessionRequest(StrictProtocolModel):
    """删除专业 Agent Session 的请求"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier


class ArtifactReference(StrictProtocolModel):
    """沙箱内可验证产物的引用"""

    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
    ]
    media_type: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
        ]
        | None
    ) = None
    description: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
        ]
        | None
    ) = None

    @field_validator("path")
    @classmethod
    def validate_sandbox_path(cls, value: str) -> str:
        """只接受规范化的沙箱绝对路径"""
        if not value.startswith("/"):
            raise ValueError("产物路径必须是绝对沙箱路径")
        segments = value.split("/")
        if any(segment in {".", ".."} for segment in segments):
            raise ValueError("产物路径不能包含相对路径点段")
        if "//" in value or "\x00" in value:
            raise ValueError("产物路径必须是规范化路径")
        return value


class RepairRequest(StrictProtocolModel):
    """下游 Session 向 Planner 报告的上游修补需求"""

    target_agent_type: AgentType
    target_session_id: Identifier
    reason: NonEmptyText
    expected_result: NonEmptyText


class AgentResult(StrictProtocolModel):
    """专业 Agent 与委派工具共用的结构化结果"""

    status: Literal["completed", "needs_repair", "failed"]
    content: NonEmptyText
    artifacts: Annotated[list[ArtifactReference], Field(max_length=50)] = Field(
        default_factory=list
    )
    repair_requests: Annotated[list[RepairRequest], Field(max_length=50)] = Field(
        default_factory=list
    )
    failure_reasons: Annotated[list[NonEmptyText], Field(max_length=50)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """校验状态与结果载荷的一致性"""
        if self.status == "needs_repair" and not self.repair_requests:
            raise ValueError("needs_repair 状态必须包含至少一个修补请求")
        if self.status != "needs_repair" and self.repair_requests:
            raise ValueError("修补请求仅在 needs_repair 状态下有效")
        if self.status == "failed" and not self.failure_reasons:
            raise ValueError("failed 状态必须包含至少一个失败原因 (failure_reasons)")
        if self.status != "failed" and self.failure_reasons:
            raise ValueError("失败原因仅在 failed 状态下有效")
        return self


class SpecialistResult(AgentResult):
    """所有专业 Agent 的结构化输出"""


class DelegationResult(AgentResult):
    """delegation 返回给 Planner 的稳定协议"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier


class SessionSummary(StrictProtocolModel):
    """单个专业 Agent Session 的结构化摘要"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    status: Literal[
        "active",
        "completed",
        "needs_repair",
        "failed",
        "interrupted",
    ]
    summary: NonEmptyText | None = None
    artifact_count: int = Field(default=0, ge=0)
    updated_at: datetime | None = None


class ListSessionsResult(StrictProtocolModel):
    """当前 Conversation 内的专业 Session 列表"""

    analysis_id: Identifier | None = None
    sessions: list[SessionSummary]


class DeleteSessionResult(StrictProtocolModel):
    """删除专业 Agent Session 的成功响应"""

    status: Literal["success"] = "success"
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    existed: bool
    message: NonEmptyText
