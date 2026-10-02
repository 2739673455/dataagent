"""消息分块、围栏及空增量的投影回归。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.assistant.services.message_projection import langchain_message_to_schema
from app.assistant.services.message_stream import MessageDeltaParser


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fence_and_directive_may_span_content_blocks_without_mutating_checkpoint(fence):
    hidden = "/data/hidden.csv"
    visible = "/data/result.csv"
    text = f"{fence}text\n[[DATAAGENT_ARTIFACT:{hidden}]]\n{fence}\n结果\n[[DATAAGENT_ARTIFACT:{visible}]]\n"
    # 每字符一块，同时覆盖拆开的开闭围栏、指令和换行。
    message = AIMessage(content=[{"type": "text", "text": char} for char in text])
    original = message.model_dump()
    files = MagicMock(
        resolve_artifacts=AsyncMock(
            return_value={visible: SimpleNamespace(relative_path="result.csv")}
        )
    )
    result = asyncio.run(langchain_message_to_schema(message, files, 7, uuid4()))
    assert result is not None
    files.resolve_artifacts.assert_awaited_once()
    assert files.resolve_artifacts.call_args.args[2] == [visible]
    assert "".join(
        part.text for part in result.parts if part.type == "text"
    ) == text.replace(f"[[DATAAGENT_ARTIFACT:{visible}]]\n", "")
    assert result.attachments and result.attachments[0].f_path == "result.csv"
    assert message.model_dump() == original


def test_empty_deltas_do_not_consume_first_reset_for_either_channel():
    parser = MessageDeltaParser()
    assert (
        list(
            parser.parse(
                (
                    AIMessageChunk(
                        id="m", content="", additional_kwargs={"reasoning_content": ""}
                    ),
                    {},
                )
            )
        )
        == []
    )
    first = list(
        parser.parse(
            (
                AIMessageChunk(
                    id="m",
                    content="回答",
                    additional_kwargs={"reasoning_content": "思考"},
                ),
                {},
            )
        )
    )
    assert [(kind, payload["reset"]) for kind, payload in first] == [
        ("thinking", True),
        ("text", True),
    ]
    second = list(
        parser.parse(
            (
                AIMessageChunk(
                    id="m",
                    content="继续",
                    additional_kwargs={"reasoning_content": "继续"},
                ),
                {},
            )
        )
    )
    assert all(not payload["reset"] for _, payload in second)
