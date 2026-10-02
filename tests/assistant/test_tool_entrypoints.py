"""通过 ToolNode 检查模型校验、运行时注入和取消传播。"""

import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ValidationError

from app.assistant.agents.tools.delegation import create_delegation_tools
from app.assistant.agents.tools.execute_sql import create_execute_sql_tool
from app.assistant.agents.tools.semantic_recall import (
    create_semantic_recall_tools,
)
from app.assistant.agents.tools.shell import create_shell_tools
from app.assistant.agents.tools.view_image import (
    ImageViewRequest,
    create_view_image_tool,
)


async def _invoke(tool, args):
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    result = await builder.compile().ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool.name,
                            "id": "test-call",
                            "type": "tool_call",
                            "args": args,
                        }
                    ],
                )
            ]
        },
        {"configurable": {"user_id": 7, "conversation_id": str(uuid4())}},
    )
    return result["messages"][-1]


@pytest.mark.parametrize(
    "kind",
    [
        "delegation",
        "list",
        "delete",
        "recall",
        "merge",
        "delete_recalls",
        "sql",
        "shell",
        "image",
    ],
)
def test_invalid_tool_arguments_never_execute_business(kind):
    service = MagicMock()
    if kind == "delegation":
        tool, args = (
            create_delegation_tools(service)[0],
            {
                "analysis_id": "INVALID",
                "agent_type": "analyst",
                "session_id": "s",
                "message": "work",
            },
        )
    elif kind == "list":
        tool, args = create_delegation_tools(service)[1], {"analysis_id": "INVALID"}
    elif kind == "delete":
        tool, args = (
            create_delegation_tools(service)[2],
            {"analysis_id": "a", "agent_type": "analyst", "session_id": ""},
        )
    elif kind == "recall":
        tool, args = (
            create_semantic_recall_tools(service)[0],
            {"query": "q", "resource_types": ["column"], "terms": [" "]},
        )
    elif kind == "merge":
        tool, args = (
            create_semantic_recall_tools(service)[3],
            {"target_query": "q", "source_query": " q "},
        )
    elif kind == "delete_recalls":
        tool, args = (
            create_semantic_recall_tools(service)[4],
            {"deletions": [{"query": "q"}, {"query": " q "}]},
        )
    elif kind == "sql":
        tool, args = create_execute_sql_tool(service), {"sql": 42, "purpose": "统计"}
    elif kind == "shell":
        tool, args = create_shell_tools(service)[2], {"job_id": "j", "wait_seconds": 61}
    else:
        tool, args = create_view_image_tool("/data/conversation"), {"f_path": " "}
    message = asyncio.run(_invoke(tool, args))
    assert message.status == "error"
    assert "Error invoking tool" in message.content
    assert not service.mock_calls


def test_delegation_schema_injects_runtime_and_normalizes_request_once():
    service = MagicMock(
        execute_delegation=AsyncMock(
            return_value=MagicMock(model_dump=lambda **_: {"status": "completed"})
        )
    )
    tool = create_delegation_tools(service)[0]
    assert (
        "runtime"
        not in cast(type[BaseModel], tool.tool_call_schema).model_json_schema()[
            "properties"
        ]
    )
    message = asyncio.run(
        _invoke(
            tool,
            {
                "analysis_id": "analysis",
                "agent_type": "analyst",
                "session_id": "session",
                "message": "  分析  ",
            },
        )
    )
    assert json.loads(message.content) == {"status": "completed"}
    call = service.execute_delegation.await_args
    assert call.args[0].message == "分析"
    assert call.kwargs["delegation_id"] == "test-call"
    assert callable(call.kwargs["activity_writer"])


def test_recall_normalizes_schema_and_passes_only_search_fields_to_metadata():
    service = MagicMock(recall_context=AsyncMock(return_value=MagicMock(query="收入")))
    tool = create_semantic_recall_tools(service)[0]
    payload = {"status": "success", "mode": "full", "query": "收入"}
    with patch(
        "app.assistant.agents.tools.semantic_recall.semantic_recall_update",
        return_value=payload,
    ):
        message = asyncio.run(
            _invoke(
                tool,
                {
                    "query": " 收入 ",
                    "resource_types": ["column", "column"],
                    "terms": [" 金额 ", "金额"],
                    "limit_per_type": 3,
                },
            )
        )
    assert json.loads(message.content) == payload
    user_id, _, query, request = service.recall_context.await_args.args
    assert user_id == 7 and query == "收入"
    assert request.model_dump() == {
        "terms": ["金额"],
        "resource_types": ["column"],
        "limit_per_type": 3,
    }


@pytest.mark.parametrize("kind", ["recall", "delegation", "shell"])
def test_tool_cancellation_is_not_converted_to_business_error(kind):
    entered = asyncio.Event()

    async def wait_for_cancel(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    cancel = AsyncMock(side_effect=wait_for_cancel)
    if kind == "recall":
        tool = create_semantic_recall_tools(MagicMock(recall_context=cancel))[0]
        args = {"query": "q", "resource_types": ["column"], "terms": ["收入"]}
    elif kind == "delegation":
        tool = create_delegation_tools(MagicMock(execute_delegation=cancel))[0]
        args = {
            "analysis_id": "a",
            "agent_type": "analyst",
            "session_id": "s",
            "message": "分析",
        }
    else:
        tool = create_shell_tools(MagicMock(start=cancel))[0]
        args = {"command": "sleep 1"}

    async def run():
        task = asyncio.create_task(_invoke(tool, args))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    cancel.assert_awaited_once()


def test_image_tool_normalizes_path_but_stored_payload_is_still_validated():
    tool = create_view_image_tool("/data/conversation")
    assert tool.invoke({"f_path": " ./chart.png "}) == {
        "type": "image_view_request",
        "f_path": "/data/conversation/chart.png",
    }
    assert tool.invoke({"f_path": "report.csv"})["code"] == "unsupported_image_type"
    with pytest.raises(ValidationError):
        ImageViewRequest.model_validate_json(
            '{"type":"image_view_request","f_path":" "}'
        )


def test_image_tool_resolves_session_paths_and_rejects_invalid_text():
    tool = create_view_image_tool("/data/conversation/sessions/analyst/session")
    assert tool.invoke({"f_path": "tmp/../chart.png"})["f_path"] == (
        "/data/conversation/sessions/analyst/session/chart.png"
    )
    assert tool.invoke({"f_path": "/skills/chart.png"})["f_path"] == "/skills/chart.png"
    assert tool.invoke({"f_path": "chart\\image.png"})["code"] == "invalid_path"
