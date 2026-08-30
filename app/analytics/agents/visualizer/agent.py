"""可视化 Agent 构造器"""

from collections.abc import Sequence
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.analytics.agents.contracts import SpecialistResult
from app.analytics.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.analytics.agents.middleware.user_message_attachments import (
    UserMessageAttachmentMiddleware,
)
from app.analytics.agents.shell_jobs import ShellJobContextMiddleware, ShellJobRuntime
from app.analytics.agents.skills import mount_agent_skills
from app.analytics.agents.tools import (
    create_image_view_request_tool,
    create_shell_tools,
)
from app.analytics.agents.visualizer.prompt import VISUALIZER_SYSTEM_PROMPT


def create_visualizer_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译可视化 Agent"""
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
        system_prompt=VISUALIZER_SYSTEM_PROMPT,
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
        name="visualizer",
    )
