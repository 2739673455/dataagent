from asyncio import Lock
from pathlib import Path
from typing import Any

from app.config import CFG
from app.core.mcp import mcp_client
from app.core.tools import db_query, return_file
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langgraph.config import get_config
from langgraph.graph.state import CompiledStateGraph

# 路径常量
CURRENT_DIR = Path(__file__).parent  # core
UP1_DIR = CURRENT_DIR.parent  # app
UP2_DIR = UP1_DIR.parent  # 项目根目录

DEEPAGENTS_ROOT = UP2_DIR / ".deepagents"
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"
WORKSPACES_DIR = DEEPAGENTS_ROOT / "workspaces"

# 全局 Agent 实例
_agent: CompiledStateGraph | None = None
_agent_lock = Lock()


def get_workspace_dir(user_id: int, conversation_id: int) -> Path:
    """获取并确保用户会话工作区目录存在"""
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / str(conversation_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def _backend_factory(rt: Any) -> CompositeBackend:
    """根据运行时配置动态创建工作区后端"""
    config = get_config()
    configurable = config.get("configurable", {})
    workspace_dir = configurable.get("workspace_dir")
    if workspace_dir is None:
        raise ValueError("workspace_dir not found in config")

    # 工作区文件系统后端
    workspace_backend = LocalShellBackend(
        root_dir=Path(workspace_dir), virtual_mode=True, inherit_env=True
    )

    # 技能文件系统后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    return CompositeBackend(
        default=workspace_backend, routes={"/skills/": skills_backend}
    )


async def _build_agent() -> CompiledStateGraph:
    """创建全局复用的 Agent 实例"""
    # 模型
    model_cfg = CFG.lm_config.models[CFG.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        profile=model_cfg.profile,
        **model_cfg.params,
    )

    # MCP 工具
    mcp_tools = await mcp_client.get_tools()

    # 所有工具
    tools = [db_query, return_file, *mcp_tools]

    # 创建 Agent
    agent = create_deep_agent(
        model=model,
        tools=tools,
        backend=_backend_factory,
        skills=["/skills/"],
    )

    return agent


async def get_agent() -> CompiledStateGraph:
    """获取全局复用的 Agent 实例，不存在时按需创建"""
    global _agent
    if _agent is not None:
        return _agent

    async with _agent_lock:
        if _agent is None:
            _agent = await _build_agent()
        return _agent
