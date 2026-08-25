"""跨分析模块共享的会话标识"""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

type AgentType = Literal[
    "explorer",
    "analyst",
    "reviewer",
    "visualizer",
]

AGENT_TYPES: tuple[AgentType, ...] = (
    "explorer",
    "analyst",
    "reviewer",
    "visualizer",
)

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_agent_type(value: str) -> AgentType:
    """校验并收窄专业 Agent 类型"""
    if value not in AGENT_TYPES:
        raise ValueError(f"未知的智能体类型: {value}")
    return value


def validate_identifier(value: str, field_name: str) -> str:
    """校验 Analysis 和 Session 标识"""
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} 必须以字母或数字开头，且仅包含 1-64 位小写字母、数字、下划线或连字符"
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
        """校验用户及专业 Agent Session 标识"""
        if isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("user_id 必须为正整数")
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
