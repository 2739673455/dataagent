"""专业 Agent 公共构造逻辑"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agents.contracts import AgentType, SpecialistResult
from app.conf.app_config import cfg

_SESSION_SHELL_GUIDANCE = """
Shell 路径规则：
- execute 默认位于当前 Agent Session 目录，当前 Session 文件优先使用相对路径
- /analyses 开头的路径是文件工具和结果协议使用的虚拟路径
- execute 读取其他 Session 的虚拟路径时，使用 "$DATAAGENT_CONVERSATION_ROOT/analyses/..."
- SpecialistResult 中只返回 /analyses 开头的虚拟路径，不返回容器实际路径
""".strip()


def create_specialist_agent(
    *,
    agent_type: AgentType,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    skills: Sequence[str],
    middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
) -> CompiledStateGraph:
    """使用共享持久化资源编译专业 Agent"""
    filesystem = FilesystemMiddleware(
        backend=backend,
        tools="all",
        max_execute_timeout=cfg.sandbox.execute_timeout_seconds,
    )
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=f"{system_prompt}\n\n{_SESSION_SHELL_GUIDANCE}",
        middleware=cast(
            "Sequence[AgentMiddleware[Any, Any, Any]]",
            [filesystem, *middleware],
        ),
        backend=backend,
        skills=list(skills),
        subagents=[],
        response_format=SpecialistResult,
        checkpointer=checkpointer,
        store=store,
        name=agent_type,
    )
