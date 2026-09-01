"""DeepSeek thinking 工具调用协议测试。"""

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableBinding
from langchain_core.tools import tool
from pydantic import SecretStr

from app.assistant.deepseek_model import (
    DataAgentChatDeepSeek,
    DataAgentDeepSeekResponses,
)
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


def test_model_factory_configures_responses_without_provider_storage() -> None:
    configured_model = cfg.lm_config.models[cfg.lm_config.active]
    model_cfg = configured_model.model_copy(
        update={
            "profile": configured_model.profile.model_copy(
                update={"image_inputs": True}
            )
        }
    )
    with (
        patch.dict(cfg.lm_config.models, {cfg.lm_config.active: model_cfg}),
        patch("app.assistant.model_factory.DataAgentDeepSeekResponses") as model_class,
    ):
        create_configured_model(cfg.lm_config.active)

    kwargs = model_class.call_args.kwargs
    api_key = kwargs["api_key"]
    assert isinstance(api_key, str)
    assert kwargs["use_responses_api"] is True
    assert kwargs["output_version"] == "responses/v1"
    assert kwargs["store"] is False
    assert kwargs["use_previous_response_id"] is False
    assert kwargs["profile"]["image_inputs"] is True
    assert kwargs["profile"]["image_tool_message"] is True


def test_deepseek_responses_replays_plaintext_reasoning_without_storage() -> None:
    model = DataAgentDeepSeekResponses(
        model="deepseek-v4-flash",
        api_key=SecretStr("test-key"),
        base_url="https://api.deepseek.com",
        use_responses_api=True,
        output_version="responses/v1",
        store=False,
    )
    messages = [
        HumanMessage("查询数据"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "需要先定位数据源"}],
                }
            ],
            tool_calls=[
                {
                    "name": "lookup",
                    "args": {"query": "GMV"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="查询完成", tool_call_id="call-1"),
    ]

    payload = model._get_request_payload(messages, tools=[])

    assert payload["store"] is False
    assert any(item.get("type") == "reasoning" for item in payload["input"])


def test_deepseek_responses_serializes_image_tool_output() -> None:
    model = DataAgentDeepSeekResponses(
        model="deepseek-v4-flash-vision-exp",
        api_key=SecretStr("test-key"),
        base_url="https://api.deepseek.com",
        use_responses_api=True,
        output_version="responses/v1",
        store=False,
    )
    messages = [
        HumanMessage("查看图片"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "view_image",
                    "args": {"f_path": "/uploads/chart.png"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=[
                {"type": "text", "text": "图片路径：`/uploads/chart.png`"},
                {
                    "type": "image",
                    "base64": "aW1hZ2U=",
                    "mime_type": "image/png",
                },
            ],
            tool_call_id="call-1",
        ),
    ]

    payload = model._get_request_payload(messages, tools=[])
    tool_output = next(
        item for item in payload["input"] if item.get("type") == "function_call_output"
    )

    assert tool_output["output"] == [
        {"type": "input_text", "text": "图片路径：`/uploads/chart.png`"},
        {
            "type": "input_image",
            "image_url": "data:image/png;base64,aW1hZ2U=",
        },
    ]


def test_model_factory_keeps_chat_completions_provider_adapter() -> None:
    model_name = "openrouter-deepseek-v4-flash"
    configured_model = cfg.lm_config.models[model_name]
    model_cfg = configured_model.model_copy(
        update={
            "profile": configured_model.profile.model_copy(
                update={"image_inputs": True}
            )
        }
    )
    with (
        patch.dict(cfg.lm_config.models, {model_name: model_cfg}),
        patch("app.assistant.model_factory.init_chat_model") as model_factory,
    ):
        create_configured_model(model_name)

    kwargs = model_factory.call_args.kwargs
    assert kwargs["model_provider"] == "openai"
    assert "use_responses_api" not in kwargs
    assert kwargs["profile"]["image_inputs"] is True
    assert kwargs["profile"]["image_tool_message"] is False
