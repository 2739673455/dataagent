"""委派工具的错误响应测试"""

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from langchain.tools import ToolRuntime

from app.analytics.agents.planner.delegation import create_delegation_tool


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


class DelegationToolTest(unittest.IsolatedAsyncioTestCase):
    """验证委派工具将入口错误转为结构化结果"""

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

        result = await cast(Any, tool).coroutine(
            runtime=make_runtime(),
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
