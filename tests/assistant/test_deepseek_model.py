"""DeepSeek Responses 协议测试。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import SecretStr

from app.assistant.deepseek_model import DataAgentDeepSeekResponses


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
