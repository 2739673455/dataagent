"""DeepSeek thinking 工具调用协议测试。"""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableBinding
from langchain_core.tools import tool
from pydantic import SecretStr

from app.assistant.deepseek_model import DataAgentChatDeepSeek
from app.assistant.model_factory import create_configured_model
from app.shared.config.app_config import cfg


def _model(*, thinking_disabled: bool = False) -> DataAgentChatDeepSeek:
    if thinking_disabled:
        return DataAgentChatDeepSeek(
            model="deepseek-v4-flash",
            api_key=SecretStr("test-key"),
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "disabled"}},
        )
    return DataAgentChatDeepSeek(
        model="deepseek-v4-flash",
        api_key=SecretStr("test-key"),
        base_url="https://api.deepseek.com",
    )


def test_request_payload_preserves_reasoning_content() -> None:
    model = _model()
    messages = [
        HumanMessage("查询数据"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup",
                    "args": {"query": "GMV"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
            additional_kwargs={"reasoning_content": "需要先定位数据源"},
        ),
        ToolMessage(content="查询完成", tool_call_id="call-1"),
    ]

    payload = model._get_request_payload(messages, tools=[])

    assert payload["messages"][1]["reasoning_content"] == "需要先定位数据源"


def test_thinking_mode_drops_forced_tool_choice() -> None:
    model = _model()

    @tool
    def lookup(query: str) -> str:
        """查询数据。"""
        return query

    bound = model.bind_tools([lookup], tool_choice="any")

    assert isinstance(bound, RunnableBinding)
    assert "tool_choice" not in bound.kwargs


def test_non_thinking_mode_keeps_forced_tool_choice() -> None:
    model = _model(thinking_disabled=True)

    @tool
    def lookup(query: str) -> str:
        """查询数据。"""
        return query

    bound = model.bind_tools([lookup], tool_choice="any")

    assert isinstance(bound, RunnableBinding)
    assert bound.kwargs["tool_choice"] == "required"


def test_model_factory_unwraps_api_key_at_client_boundary() -> None:
    with patch("app.assistant.model_factory.DataAgentChatDeepSeek") as model_class:
        create_configured_model(cfg.lm_config.active)

    api_key = model_class.call_args.kwargs["api_key"]
    assert isinstance(api_key, str)
