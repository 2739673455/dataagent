"""Dynamic Subagents 的公共协议"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

type AgentType = Literal[
    "data_query",
    "attribution",
    "anomaly_detection",
    "visualization",
]

AGENT_TYPES: tuple[AgentType, ...] = (
    "data_query",
    "attribution",
    "anomaly_detection",
    "visualization",
)

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=_IDENTIFIER_PATTERN.pattern,
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID"""
    if isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("user_id must be a positive integer")
    return f"user_{user_id}:conversation_{conversation_id}"


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
    """绑定一个用户回合的 Planner 预算和续写上限"""

    user_id: int
    conversation_id: UUID
    planner_run_id: str
    max_continuations: int

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        if not self.planner_run_id.strip():
            raise ValueError("planner_run_id must not be empty")
        if self.max_continuations < 0:
            raise ValueError("max_continuations must not be negative")


def validate_agent_type(value: str) -> AgentType:
    """校验并收窄专业 Agent 类型"""
    if value not in AGENT_TYPES:
        raise ValueError(f"unknown agent type: {value}")
    return value


def validate_identifier(value: str, field_name: str) -> str:
    """校验 Analysis 和 Session 标识"""
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must contain 1-64 lowercase letters, digits, hyphens, "
            "or underscores and start with a letter or digit"
        )
    return value


@dataclass(frozen=True, slots=True)
class AgentSessionKey:
    """定位一个可续接的专业 Agent Session"""

    user_id: int
    conversation_id: UUID
    analysis_id: str
    agent_type: AgentType
    session_id: str

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        validate_identifier(self.analysis_id, "analysis_id")
        validate_agent_type(self.agent_type)
        validate_identifier(self.session_id, "session_id")

    @property
    def checkpoint_ns(self) -> str:
        """生成受控的 Checkpoint namespace"""
        return f"subagents/{self.analysis_id}/{self.agent_type}/{self.session_id}"

    @property
    def workspace_dir(self) -> str:
        """生成专业 Agent Session 的虚拟工作目录"""
        return (
            f"/analyses/{self.analysis_id}/sessions/{self.agent_type}/{self.session_id}"
        )


class StrictProtocolModel(BaseModel):
    """拒绝未知字段的协议模型基类"""

    model_config = ConfigDict(extra="forbid", strict=True)


class DelegateAgentRequest(StrictProtocolModel):
    """Planner 发起专业 Agent 委派的请求"""

    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    message: NonEmptyText
    repair_depth: int = Field(default=0, ge=0)


class ArtifactReference(StrictProtocolModel):
    """沙盒内可验证产物的引用"""

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
        """只接受规范化的沙盒绝对路径"""
        if not value.startswith("/"):
            raise ValueError("artifact path must be an absolute sandbox path")
        segments = value.split("/")
        if any(segment in {".", ".."} for segment in segments):
            raise ValueError("artifact path must not contain dot segments")
        if "//" in value or "\x00" in value:
            raise ValueError("artifact path must be normalized")
        return value


class RepairRequest(StrictProtocolModel):
    """下游 Session 向 Planner 报告的上游修补需求"""

    target_agent_type: AgentType
    target_session_id: Identifier
    reason: NonEmptyText
    evidence: Annotated[list[ArtifactReference], Field(min_length=1, max_length=20)]
    expected_result: NonEmptyText


class SpecialistResult(StrictProtocolModel):
    """所有专业 Agent 的结构化输出"""

    status: Literal["completed", "needs_repair", "failed"]
    summary: NonEmptyText
    findings: Annotated[list[NonEmptyText], Field(max_length=100)] = Field(
        default_factory=list
    )
    artifacts: Annotated[list[ArtifactReference], Field(max_length=100)] = Field(
        default_factory=list
    )
    repair_requests: Annotated[list[RepairRequest], Field(max_length=20)] = Field(
        default_factory=list
    )
    confidence: Literal["low", "medium", "high"] | None = None
    limitations: Annotated[list[NonEmptyText], Field(max_length=100)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """校验状态与证据载荷的一致性"""
        if self.status == "needs_repair" and not self.repair_requests:
            raise ValueError("needs_repair requires at least one repair request")
        if self.status != "needs_repair" and self.repair_requests:
            raise ValueError("repair requests are only valid for needs_repair")
        if self.status == "completed" and not self.findings:
            raise ValueError("completed requires at least one finding")
        if self.status == "completed" and not self.artifacts:
            raise ValueError("completed requires at least one artifact")
        if self.status == "failed" and not self.limitations:
            raise ValueError("failed requires at least one limitation")
        return self


class DelegateAgentResult(StrictProtocolModel):
    """delegate_agent 返回给 Planner 的稳定协议"""

    status: Literal["completed", "needs_repair", "failed"]
    analysis_id: Identifier
    agent_type: AgentType
    session_id: Identifier
    summary: NonEmptyText
    findings: Annotated[list[NonEmptyText], Field(max_length=100)] = Field(
        default_factory=list
    )
    artifacts: Annotated[list[ArtifactReference], Field(max_length=100)] = Field(
        default_factory=list
    )
    repair_requests: Annotated[list[RepairRequest], Field(max_length=20)] = Field(
        default_factory=list
    )
    confidence: Literal["low", "medium", "high"] | None = None
    limitations: Annotated[list[NonEmptyText], Field(max_length=100)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        """校验委派结果状态与载荷的一致性"""
        if self.status == "needs_repair" and not self.repair_requests:
            raise ValueError("needs_repair requires at least one repair request")
        if self.status != "needs_repair" and self.repair_requests:
            raise ValueError("repair requests are only valid for needs_repair")
        if self.status == "completed" and not self.findings:
            raise ValueError("completed requires at least one finding")
        if self.status == "completed" and not self.artifacts:
            raise ValueError("completed requires at least one artifact")
        if self.status == "failed" and not self.limitations:
            raise ValueError("failed requires at least one limitation")
        return self
