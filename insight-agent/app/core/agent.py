from app.config import CFG
from app.core.mcp import mcp_client
from app.core.tools import create_db_query_tool, create_return_file_tool
from app.utils.agent_paths import SKILLS_DIR, get_workspace_dir
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langgraph.graph.state import CompiledStateGraph


async def build_agent(user_id: int, conversation_id: int) -> CompiledStateGraph:
    # 模型
    model_cfg = CFG.lm_config.models[CFG.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        **model_cfg.params,
    )

    # 文件后端，为每个用户/会话创建独立工作目录
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    workspace_backend = LocalShellBackend(
        root_dir=workspace_dir, virtual_mode=True, inherit_env=True
    )

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills_backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    backend = CompositeBackend(
        default=workspace_backend, routes={"/skills/": skills_backend}
    )

    # 挂载技能目录
    skills = ["/skills/"]

    # MCP 工具
    mcp_tools = await mcp_client.get_tools()

    # 所有工具
    tools = [
        create_db_query_tool(workspace_dir),
        create_return_file_tool(workspace_dir),
        *mcp_tools,
    ]

    # 创建 Agent
    agent = create_deep_agent(model=model, tools=tools, backend=backend, skills=skills)

    return agent
