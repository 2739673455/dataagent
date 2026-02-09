import asyncio
import os

from app.core.mcp import mcp_client
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain.messages import AIMessage

params = {}

model = init_chat_model(
    model_provider="openai",
    model="openai/gpt-4o-mini",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    **params,
)


async def main():
    tools = await mcp_client.get_tools()
    agent = create_deep_agent(model=model, tools=tools)

    async for chunk in agent.astream(
        input={"messages": [{"role": "user", "content": "现在流行的ai编程工具有哪些?"}]}
    ):
        for k, v in chunk.items():
            if v is None:
                continue
            try:
                if "messages" in v and isinstance(
                    message := v["messages"][0], AIMessage
                ):
                    print(
                        k,
                        "\n",
                        {"content": message.content, "tool_calls": message.tool_calls},
                        end="\n\n",
                    )
                else:
                    print(k, "\n", v, end="\n\n")
            except Exception:
                print(k, "\n", v, end="\n\n")


asyncio.run(main())
