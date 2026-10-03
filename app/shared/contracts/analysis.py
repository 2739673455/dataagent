"""分析标识格式与配置使用的专家类型词汇。"""

import re
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

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_agent_type(value: str) -> AgentType:
    """校验并收窄专业 Agent 类型。"""
    if value not in AGENT_TYPES:
        raise ValueError(f"未知的智能体类型: {value}")
    return value
