from pathlib import Path

from app.config import CFG
from app.core.mcp import mcp_client
from app.core.tools import create_db_query_tool
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langgraph.graph.state import CompiledStateGraph

# 路径常量
CURRENT_DIR = Path(__file__).parent  # core
UP1_DIR = CURRENT_DIR.parent  # app
UP2_DIR = UP1_DIR.parent  # 项目根目录

DEEPAGENTS_ROOT = UP2_DIR / ".deepagents"  # deepagents 文件后端目录
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"  # 技能目录
WORKSPACES_DIR = DEEPAGENTS_ROOT / "workspaces"  # 工作区目录


async def build_agent(user_id: int, conversation_id: str) -> CompiledStateGraph:
    # 模型
    model_cfg = CFG.lm_config.models[CFG.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        **model_cfg.params,
    )

    # 文件后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    # 为每个用户/会话创建独立工作目录
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / conversation_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    workspace_backend = LocalShellBackend(
        root_dir=workspace_dir, virtual_mode=True, inherit_env=True
    )
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    backend = CompositeBackend(
        default=workspace_backend, routes={"/skills/": skills_backend}
    )

    # 挂载技能目录
    skills = ["/skills/"]

    # MCP 工具
    mcp_tools = await mcp_client.get_tools()

    # 所有工具
    tools = [create_db_query_tool(workspace_dir), *mcp_tools]

    # 创建 Agent
    agent = create_deep_agent(model=model, tools=tools, backend=backend, skills=skills)

    return agent
