"""会话线程标识、锁名称与 Planner 运行配置。"""

from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.assistant.models.session import AgentSessionKey
from app.sandbox import conversation_workspace_path


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID。"""
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


def session_checkpoint_namespace(session_key: AgentSessionKey) -> str:
    """将专业 Session 身份映射到 Assistant 私有检查点 namespace。"""
    return f"subagents/{session_key.analysis_id}/{session_key.agent_type}/{session_key.session_id}"
