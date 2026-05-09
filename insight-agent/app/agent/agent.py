from asyncio import Lock
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langgraph.config import get_config
from langgraph.graph.state import CompiledStateGraph

from app.agent.mcp import get_mcp_tools
from app.agent.tools import db_query, return_file
from app.core import settings

# 路径常量
CURRENT_DIR = Path(__file__).parent  # agent
ROOT_DIR = CURRENT_DIR.parent.parent  # 项目根目录
DEEPAGENTS_ROOT = ROOT_DIR / ".deepagents"
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
    # 从 LangGraph 运行时配置中获取工作区目录
    workspace_dir = get_config().get("configurable", {}).get("workspace_dir")
    if workspace_dir is None:
        raise ValueError("workspace_dir not found in config")

    # 工作区文件系统后端
    workspace_backend = LocalShellBackend(
        root_dir=Path(workspace_dir), virtual_mode=True, inherit_env=True
    )

    # 技能文件系统后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    # CompositeBackend 将多个后端合并为一个统一视图，对 Agent 透明：
    # - default: 工作区后端（LocalShellBackend），处理除 /skills/ 外的所有路径
    # - routes["/skills/"]: 命中此前缀时，剥离前缀后将剩余路径转发到 skills_backend
    #   例如 Agent 请求 /skills/insight/SKILL.md → skills_backend 收到 insight/SKILL.md
    return CompositeBackend(default=workspace_backend, routes={"/skills/": skills_backend})


async def _build_agent() -> CompiledStateGraph:
    """创建 Agent 实例"""
    # 初始化模型
    model_cfg = settings.cfg.lm_config.models[settings.cfg.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        profile=model_cfg.profile,
        request_timeout=30,
        max_retries=2,
        **model_cfg.params,
    )

    # 加载工具
    mcp_tools = await get_mcp_tools()
    tools = [db_query, return_file, *mcp_tools]

    agent = create_deep_agent(
        model=model,
        tools=tools,
        backend=_backend_factory,  # 根据运行时配置动态创建工作区后端
        skills=["/skills/"],  # 声明 Agent 可用的 Skill 前缀路径
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


async def reset_agent() -> None:
    """使当前 Agent 实例失效，下次 get_agent() 将用最新配置重建"""
    global _agent
    async with _agent_lock:
        _agent = None
