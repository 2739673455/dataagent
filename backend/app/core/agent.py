import asyncio

from app.config import CFG
from app.core.mcp import mcp_client
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage


def process_messages(message: dict):
    # 模型输出
    if "model" in message:
        model_messages: list = message["model"]["messages"]
        ai_message: AIMessage = model_messages[-1]

        content = ai_message.content
        tool_calls = ai_message.tool_calls
        finish_reason = ai_message.response_metadata["finish_reason"]

        print({"content": content, "tool_calls": tool_calls}, "\n")

        return ai_message

    # 工具输出
    elif "tools" in message:
        tool_messages: list = message["tools"]["messages"]
        tool_message = tool_messages[-1]

        print(tool_messages, "\n")

        return tool_message

    # 中间件输出
    else:
        print(message, "\n")


async def build_agent():
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

    tools = [*mcp_tools]

    # 文件系统后端
    backend = LocalShellBackend(
        root_dir="/home/kodey/agents/backend/.deepagents", virtual_mode=True
    )

    # 技能目录(文件系统后端根路径下的 skills 目录)
    skills = ["/skills"]

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
