import asyncio

from app.config import CFG
from app.core.mcp import mcp_client
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage, ToolMessage


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


async def build_agent():
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

    # 文件系统后端
    backend = LocalShellBackend(
        root_dir="/home/kodey/agents/backend/.deepagents",
        virtual_mode=True,  # True: 将 root_dir 视为虚拟文件系统根目录
        inherit_env=True,  # 继承环境变量
    )

    # 技能目录(文件系统后端根路径下的 skills 目录)
    skills = ["/skills"]

    # 创建 Agent
    agent = create_deep_agent(model=model, tools=tools, backend=backend, skills=skills)

    return agent


async def main():
    agent = await build_agent()

    while True:
        user_message = input("User: ")
        if not user_message:
            continue

        messages = [{"role": "user", "content": user_message}]

        async for chunk in agent.astream(input={"messages": messages}):
            process_messages(chunk)


asyncio.run(main())
