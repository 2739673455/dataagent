"""Planner 工具的框架校验、运行时注入与业务错误响应。"""

import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from app.assistant.agents.planner.tools import (
    create_delegation_tool,
    create_delete_session_tool,
    create_list_sessions_tool,
)
from app.assistant.execution.types import DelegationResult, ListSessionsResult


async def invoke_tool(tool: BaseTool, args: dict[str, Any]) -> ToolMessage:
    """通过 Agent 使用的 ToolNode 校验并执行一次工具调用。"""
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool.name,
                            "args": args,
                            "id": "delegation-call",
                        }
                    ],
                )
            ]
        }
    )
    return cast(ToolMessage, result["messages"][-1])


class PlannerToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_requests_return_framework_errors_without_execution(self):
        service = MagicMock()
        for tool, args, field in (
            (
                create_delegation_tool(service),
                {
                    "analysis_id": "",
                    "agent_type": "explorer",
                    "session_id": "session",
                    "message": "分析数据",
                },
                "analysis_id",
            ),
            (
                create_delegation_tool(service),
                {
                    "analysis_id": "analysis",
                    "agent_type": "explorer",
                    "session_id": "session",
                },
                "message",
            ),
            (
                create_list_sessions_tool(service),
                {"analysis_id": "Invalid ID"},
                "analysis_id",
            ),
            (
                create_delete_session_tool(service),
                {
                    "analysis_id": "analysis",
                    "agent_type": "analyst",
                    "session_id": "",
                },
                "session_id",
            ),
        ):
            with self.subTest(tool=tool.name, field=field):
                result = await invoke_tool(tool, args)
                self.assertEqual(result.status, "error")
                self.assertIn(field, result.content)
                self.assertEqual(result.tool_call_id, "delegation-call")
        self.assertEqual(service.mock_calls, [])

    async def test_valid_delegation_normalizes_args_and_injects_runtime(self):
        service = MagicMock()
        service.execute_delegation = AsyncMock(
            return_value=DelegationResult(
                status="completed",
                content="已完成",
                analysis_id="analysis",
                agent_type="explorer",
                session_id="session",
            )
        )
        tool = create_delegation_tool(service)
        schema = cast(type[BaseModel], tool.tool_call_schema).model_json_schema()
        self.assertEqual(
            set(schema["properties"]),
            {
                "analysis_id",
                "agent_type",
                "session_id",
                "message",
            },
        )
        result = await invoke_tool(
            tool,
            {
                "analysis_id": " analysis ",
                "agent_type": "explorer",
                "session_id": " session ",
                "message": " 分析数据 ",
                "unknown": True,
            },
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content, "已完成")
        call = service.execute_delegation.await_args
        self.assertEqual(call.args[0].analysis_id, "analysis")
        self.assertEqual(call.args[0].session_id, "session")
        self.assertEqual(call.args[0].message, "分析数据")
        self.assertEqual(call.kwargs["delegation_id"], "delegation-call")
        self.assertTrue(callable(call.kwargs["activity_writer"]))

    async def test_list_sessions_default_is_preserved(self):
        service = MagicMock()
        service.list_sessions = AsyncMock(return_value=ListSessionsResult(sessions=[]))
        result = await invoke_tool(
            create_list_sessions_tool(service), {"unknown": True}
        )
        self.assertEqual(result.status, "success")
        service.list_sessions.assert_awaited_once_with(None)

    async def test_execution_failures_keep_business_error_details(self):
        service = MagicMock()
        service.execute_delegation = AsyncMock(
            side_effect=RuntimeError("Planner 执行状态不可用")
        )
        service.delete_session = AsyncMock(side_effect=TimeoutError("获取锁超时"))
        for tool, args, code, error_type, message in (
            (
                create_delegation_tool(service),
                {
                    "analysis_id": "analysis",
                    "agent_type": "explorer",
                    "session_id": "session",
                    "message": "分析数据",
                },
                "delegation_failed",
                "RuntimeError",
                "Planner 执行状态不可用",
            ),
            (
                create_delete_session_tool(service),
                {
                    "analysis_id": "analysis",
                    "agent_type": "analyst",
                    "session_id": "session",
                },
                "delete_session_failed",
                "TimeoutError",
                "获取锁超时",
            ),
        ):
            with self.subTest(tool=tool.name):
                result = await invoke_tool(tool, args)
                payload = json.loads(cast(str, result.content))
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["code"], code)
                self.assertEqual(
                    payload["details"], [{"type": error_type, "msg": message}]
                )
