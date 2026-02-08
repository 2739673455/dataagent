import asyncio
import os

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

agent = create_deep_agent(model=model)


async def main():
    async for chunk in agent.astream(
        input={"messages": [{"role": "user", "content": "看下你当前在哪一个目录"}]}
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


# asyncio.run(main())
