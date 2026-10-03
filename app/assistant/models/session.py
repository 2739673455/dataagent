"""专业 Agent Session 的身份；检查点地址由持久化边界转换。"""

from dataclasses import dataclass
from uuid import UUID

from app.shared.contracts.analysis import AgentType


@dataclass(frozen=True, slots=True)
class AgentSessionKey:
    """定位专业 Agent 的工作会话 Session，可由多次 Delegation 续接。

    conversation_id 属于用户聊天；analysis_id 划分一次分析；session_id
    在该分析和 agent_type 下定位独立的专家工作上下文。"""

    user_id: int
    conversation_id: UUID
    analysis_id: str
    agent_type: AgentType
    session_id: str
