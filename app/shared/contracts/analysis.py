"""跨模块共享的专业 Agent 类型。"""

from typing import Literal

type AgentType = Literal[
    "explorer",
    "analyst",
    "reviewer",
]

AGENT_TYPES: tuple[AgentType, ...] = (
    "explorer",
    "analyst",
    "reviewer",
)
