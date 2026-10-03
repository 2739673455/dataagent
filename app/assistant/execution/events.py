"""Agent 执行期间的内部活动、消息和状态事件。"""

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.messages import BaseMessage

from app.assistant.contracts import SubagentRunStatus
from app.shared.contracts.analysis import AgentType


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


@dataclass(frozen=True, slots=True)
class DelegationActivityHistory:
    """一次 delegation 的公开消息和真实执行状态。"""

    messages: list[BaseMessage]
    status: SubagentRunStatus
