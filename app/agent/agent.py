from asyncio import Lock
from pathlib import Path
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.mcp import get_mcp_tools
from app.agent.tools import return_file, search_semantics
from app.clients.langgraph_postgres_manager import langgraph_postgres_manager
from app.conf import app_config

# 路径常量
CURRENT_DIR = Path(__file__).parent  # agent
ROOT_DIR = CURRENT_DIR.parent.parent  # 项目根目录
DEEPAGENTS_ROOT = ROOT_DIR / ".deepagents"
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"
WORKSPACES_DIR = DEEPAGENTS_ROOT / "workspaces"

# 会话级 Agent 实例
_agents: dict[tuple[int, UUID], CompiledStateGraph] = {}
_agent_lock = Lock()


def get_workspace_dir(user_id: int, conversation_id: UUID) -> Path:
    """获取并确保用户会话工作区目录存在"""
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / str(conversation_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def get_thread_id(user_id: int, conversation_id: UUID) -> str:
    """构造全局唯一的 LangGraph 会话线程 ID"""
    return f"user_{user_id}:conversation_{conversation_id}"


def get_agent_config(user_id: int, conversation_id: UUID) -> RunnableConfig:
    """创建包含持久化线程和工作区的 Agent 运行配置"""
    return RunnableConfig(
        configurable={
            "thread_id": get_thread_id(user_id, conversation_id),
            "workspace_dir": str(get_workspace_dir(user_id, conversation_id)),
        }
    )


def _build_backend(workspace_dir: Path) -> CompositeBackend:
    """创建会话工作区后端"""
    # 工作区文件系统后端
    workspace_backend = LocalShellBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        inherit_env=True,
    )

    # 技能文件系统后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    # CompositeBackend 将多个后端合并为一个统一视图，对 Agent 透明：
    # - default: 工作区后端（LocalShellBackend），处理除 /skills/ 外的所有路径
    # - routes["/skills/"]: 命中此前缀时，剥离前缀后将剩余路径转发到 skills_backend
    #   例如 Agent 请求 /skills/insight/SKILL.md → skills_backend 收到 insight/SKILL.md
    return CompositeBackend(
        default=workspace_backend, routes={"/skills/": skills_backend}
    )


async def _build_agent(workspace_dir: Path) -> CompiledStateGraph:
    """创建会话级 Agent 实例"""
    # 初始化模型
    model_cfg = app_config.cfg.lm_config.models[app_config.cfg.lm_config.active]
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
    tools = [search_semantics, return_file, *mcp_tools]

    agent = create_deep_agent(
        model=model,
        tools=tools,
        backend=_build_backend(workspace_dir),
        skills=["/skills/"],  # 声明 Agent 可用的 Skill 前缀路径
        checkpointer=langgraph_postgres_manager.get_checkpointer(),
        store=langgraph_postgres_manager.get_store(),
    )

    return agent


async def get_agent(user_id: int, conversation_id: UUID) -> CompiledStateGraph:
    """获取会话级 Agent 实例，不存在时按需创建"""
    agent_key = (user_id, conversation_id)
    if agent := _agents.get(agent_key):
        return agent

    async with _agent_lock:
        if agent := _agents.get(agent_key):
            return agent
        agent = await _build_agent(get_workspace_dir(user_id, conversation_id))
        _agents[agent_key] = agent
        return agent


async def reset_agent() -> None:
    """清空会话级 Agent 实例，下次使用时按最新配置重建"""
    async with _agent_lock:
        _agents.clear()


async def delete_agent(user_id: int, conversation_id: UUID) -> None:
    """删除会话 Agent 实例及其持久化状态"""
    async with _agent_lock:
        _agents.pop((user_id, conversation_id), None)
    await langgraph_postgres_manager.delete_thread(
        get_thread_id(user_id, conversation_id)
    )
