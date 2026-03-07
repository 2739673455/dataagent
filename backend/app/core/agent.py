import asyncio

from app.config import CFG
from app.core.mcp import mcp_client
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model


async def build_agent():
    model_cfg = CFG.lm_config.models[CFG.lm_config.active]
    model = init_chat_model(
        model_provider="openai",
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        **model_cfg.params,
    )

    tools = await mcp_client.get_tools()

    agent = create_deep_agent(model=model, tools=tools)

    return agent


async def main():
    agent = await build_agent()

    async for chunk in agent.astream(
        input={"messages": [{"role": "user", "content": "现在流行的ai编程工具有哪些?"}]}
    ):
        print(chunk, end="\n\n")


asyncio.run(main())
