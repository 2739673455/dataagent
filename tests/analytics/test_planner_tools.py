"""Planner 专用工具测试"""

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from langchain.tools import ToolRuntime

from app.analytics.agents.planner.tools import (
    create_delegation_tool,
    create_delete_session_tool,
    create_list_sessions_tool,
)


def make_runtime() -> ToolRuntime:
    """构造仅供工具调用的最小运行时"""
    return ToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="delegation-call",
        store=None,
    )


class PlannerToolsTest(unittest.IsolatedAsyncioTestCase):
    """验证 Planner 工具将入口错误转为结构化结果"""

    async def test_invalid_request_returns_validation_details(self) -> None:
        tool = create_delegation_tool(MagicMock())

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            analysis_id="",
            agent_type="explorer",
            session_id="session",
            message="分析数据",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_delegation_request")
        self.assertEqual(result["details"][0]["loc"], ("analysis_id",))

    async def test_unexpected_failure_includes_exception_detail(self) -> None:
        service = MagicMock()
        service.execute_delegation = AsyncMock(
            side_effect=RuntimeError("委派预算不可用")
        )
        tool = create_delegation_tool(service)
        runtime = make_runtime()

        result = await cast(Any, tool).coroutine(
            runtime=runtime,
            analysis_id="analysis",
            agent_type="explorer",
            session_id="session",
            message="分析数据",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "delegation_failed")
        self.assertEqual(
            result["details"],
            [{"type": "RuntimeError", "msg": "委派预算不可用"}],
        )
        call = service.execute_delegation.await_args
        self.assertEqual(call.kwargs["delegation_id"], "delegation-call")
        self.assertIs(call.kwargs["activity_writer"], runtime.stream_writer)

    async def test_list_sessions_rejects_invalid_analysis_id(self) -> None:
        tool = create_list_sessions_tool(MagicMock())

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            analysis_id="Invalid ID",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_list_sessions_request")

    async def test_delete_session_rejects_invalid_request(self) -> None:
        tool = create_delete_session_tool(MagicMock())

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            analysis_id="analysis",
            agent_type="analyst",
            session_id="",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_delete_session_request")

    async def test_delete_session_includes_execution_error_detail(self) -> None:
        service = MagicMock()
        service.delete_session = AsyncMock(side_effect=TimeoutError("获取锁超时"))
        tool = create_delete_session_tool(service)

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
            analysis_id="analysis",
            agent_type="analyst",
            session_id="session",
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "delete_session_failed")
        self.assertEqual(
            result["details"],
            [{"type": "TimeoutError", "msg": "获取锁超时"}],
        )
