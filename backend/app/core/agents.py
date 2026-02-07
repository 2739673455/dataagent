from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model

params = {}

model = init_chat_model(
    model_provider="openai",
    model="claude-sonnet-4-5-20250929",
    base_url="",
    api_key="",
    **params,
)

agent = create_deep_agent(model=model)
