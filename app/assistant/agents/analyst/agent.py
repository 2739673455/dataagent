"""分析 Agent 构造器。"""

from collections.abc import Sequence
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.assistant.agents.contracts import SpecialistResult
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_attachments import (
    UserMessageAttachmentMiddleware,
)
from app.assistant.agents.shell_jobs import ShellJobContextMiddleware, ShellJobRuntime
from app.assistant.agents.skills import mount_agent_skills
from app.assistant.agents.tools import (
    create_image_view_request_tool,
    create_shell_tools,
)


def create_analyst_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译分析 Agent。"""
    backend, filesystem = mount_agent_skills(
        backend,
        Path(__file__).with_name("skills"),
        skills,
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            create_image_view_request_tool(),
            *create_shell_tools(shell_jobs),
        ],
        system_prompt=ANALYST_SYSTEM_PROMPT,
        middleware=[
            filesystem,
            UserMessageAttachmentMiddleware(backend),
            ShellJobContextMiddleware(shell_jobs),
            MessageTimestampMiddleware(),
        ],
        backend=backend,
        skills=list(skills),
        subagents=[],
        response_format=SpecialistResult,
        checkpointer=checkpointer,
        name="analyst",
    )
