import asyncio
from pathlib import Path

from app.config import CFG
from app.core.mcp import mcp_client
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, ToolMessage

# 路径常量
CURRENT_DIR = Path(__file__).parent  # core
UP1_DIR = CURRENT_DIR.parent  # app
UP2_DIR = UP1_DIR.parent  # 项目根目录

DEEPAGENTS_ROOT = UP2_DIR / ".deepagents"  # deepagents 文件后端目录
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"  # 技能目录
WORKSPACES_DIR = DEEPAGENTS_ROOT / "workspaces"  # 工作区目录


def process_messages(message: dict):
    # 模型输出
    if "model" in message:
        model_messages: list = message["model"]["messages"]
        ai_message: AIMessage = model_messages[-1]

        print(
            "AI:",
            {
                "content": ai_message.content,
                "tool_calls": ai_message.tool_calls,
            },
            "\n",
        )

        return ai_message

    # 工具输出
    elif "tools" in message:
        tool_messages: list = message["tools"]["messages"]
        tool_message: ToolMessage = tool_messages[-1]

        print("Tool:", tool_message, "\n")

        return tool_message

    # 中间件输出
    else:
        print(message, "\n")


async def build_agent(user_id: int, conversation_id: str):
    # 模型
    model_cfg = CFG.lm_config.models[CFG.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        **model_cfg.params,
    )

    # MCP 工具
    mcp_tools = await mcp_client.get_tools()

    # 所有工具
    tools = [*mcp_tools]

    # 文件后端
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    # 为每个用户/会话创建独立工作目录
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / conversation_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    workspace_backend = LocalShellBackend(
        root_dir=workspace_dir, virtual_mode=True, inherit_env=True
    )
    skills_backend = LocalShellBackend(
        root_dir=SKILLS_DIR, virtual_mode=True, inherit_env=True
    )
    backend = CompositeBackend(
        default=workspace_backend, routes={"/skills/": skills_backend}
    )

    # 技能目录挂载到共享路径 /skills
    skills = ["/skills/"]

    # 创建 Agent
    agent = create_deep_agent(model=model, tools=tools, backend=backend, skills=skills)

    return agent


async def main():
    agent = await build_agent(1, "1")

    while True:
        user_message = input("User: ")
        if not user_message:
            continue

        messages = [{"role": "user", "content": user_message}]

        async for chunk in agent.astream(input={"messages": messages}):
            process_messages(chunk)


asyncio.run(main())
