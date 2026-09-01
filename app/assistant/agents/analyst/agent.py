"""分析 Agent 构造器。"""

from collections.abc import Sequence
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agents.analyst.prompt import ANALYST_SYSTEM_PROMPT
from app.assistant.agents.filesystem import build_specialist_filesystem
from app.assistant.agents.middleware.message_timestamp import (
    MessageTimestampMiddleware,
)
from app.assistant.agents.middleware.user_message_attachments import (
    UserMessageAttachmentMiddleware,
)
from app.assistant.agents.shell_jobs import ShellJobContextMiddleware, ShellJobRuntime
from app.assistant.agents.structured_output import specialist_response_format
from app.assistant.agents.tools import (
    create_shell_tools,
    create_view_image_tools,
)
from app.sandbox.backend import DockerSandboxBackend


def create_analyst_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    backend: DockerSandboxBackend,
    checkpointer: BaseCheckpointSaver,
    shell_jobs: ShellJobRuntime,
    skills: Sequence[str] = (),
) -> CompiledStateGraph:
    """编译分析 Agent。"""
    resolved_backend, filesystem = build_specialist_filesystem(
        backend,
        Path(__file__).with_name("skills"),
        skills,
    )
    return create_deep_agent(
        model=model,
        tools=[
            *tools,
            *create_view_image_tools(model),
            *create_shell_tools(shell_jobs),
        ],
        system_prompt=ANALYST_SYSTEM_PROMPT,
        middleware=[
            filesystem,
            UserMessageAttachmentMiddleware(
                resolved_backend,
                backend.conversation_dir,
            ),
            ShellJobContextMiddleware(shell_jobs),
            MessageTimestampMiddleware(),
        ],
        backend=resolved_backend,
        skills=list(skills),
        subagents=[],
        response_format=specialist_response_format(model),
        checkpointer=checkpointer,
        name="analyst",
    )
