"""Agent 执行与实时活动协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.shared.contracts.analysis import AgentType


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID。"""
    return f"user_{user_id}:conversation_{conversation_id}"


def build_planner_config(user_id: int, conversation_id: UUID) -> RunnableConfig:
    """创建 Planner 根 namespace 的运行配置。"""
    return RunnableConfig(
        configurable={
            "thread_id": get_thread_id(user_id, conversation_id),
            "checkpoint_ns": "",
            "user_id": user_id,
            "conversation_id": str(conversation_id),
        }
    )


@dataclass(frozen=True, slots=True)
class PlannerTurnContext:
    """Turn 是处理一次用户输入的逻辑回合。

    中断后恢复仍处理同一回合；Run 则是承载执行与订阅的进程内实例。"""

    user_id: int
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class SubagentStatusActivity:
    """一次 Specialist 执行的状态变化。"""

    delegation_id: str
    agent_type: AgentType
    status: Literal["running", "completed", "failed", "cancelled"]
