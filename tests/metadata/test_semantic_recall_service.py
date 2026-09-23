"""直接返回语义召回结果的工具测试。"""

import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from app.assistant.agents.explorer.recall_runtime import SemanticRecallRuntime
from app.assistant.agents.explorer.semantic_recall_handler import recall_context
from app.assistant.agents.explorer.tools import create_semantic_recall_tools
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMatchReason,
    SemanticMetricRecallResult,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticTableContext,
    SemanticValueRecallResult,
)


def build_response(
    query: str,
    *,
    score: float,
    reason: str,
) -> SemanticResourceRecallResponse:
    """构造包含重复资源的测试召回响应。"""
    match_reason = SemanticMatchReason(
        match_type="fulltext",
        term=reason,
        score=score,
    )
    return SemanticResourceRecallResponse(
        status="success",
        terms=[query],
        metrics=[
            SemanticMetricRecallResult(
                name="revenue",
                description="收入",
                alias=[f"收入-{reason}"],
                relevant_columns=[{"t_name": "orders", "c_name": "amount"}],
                rank_score=score,
                match_reasons=[match_reason],
                index_ready=True,
            )
        ],
        columns=[
            SemanticColumnRecallResult(
                t_name="orders",
                name="amount",
                type="decimal",
                description="订单金额",
                alias=[f"金额-{reason}"],
                examples=[reason],
                reference_t_name=None,
                reference_c_name=None,
                inclusion_reasons=["direct_match"],
                rank_score=score,
                match_reasons=[match_reason],
                index_ready=True,
            )
        ],
        values=[
            SemanticValueRecallResult(
                value="paid",
                t_name="orders",
                c_name="status",
                rank_score=score,
                match_reasons=[match_reason],
                sync_status="succeeded",
            )
        ],
        tables=[
            SemanticTableContext(
                name="orders",
                role="fact",
                description="订单事实表",
                primary_key_columns=["id"],
            )
        ],
        failures=[],
        warnings=[],
        truncated=False,
    )


class SemanticRecallToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_results_remain_in_messages_across_turns(self):
        first = build_response("金额", score=0.8, reason="金额")
        first.values = [
            first.values[0].model_copy(update={"c_name": "amount", "value": "100"})
        ]
        second = first.model_copy(update={"metrics": [], "values": []})
        runtime = MagicMock(spec=SemanticRecallRuntime)
        runtime.search = AsyncMock(side_effect=[first, second])
        tools = create_semantic_recall_tools(runtime)
        self.assertEqual([tool.name for tool in tools], ["recall_context"])
        self.assertNotIn("query", tools[0].args)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        config: RunnableConfig = {
            "configurable": {"thread_id": "recall-results", "user_id": 7}
        }
        result: dict[str, Any] = {"messages": []}
        for index in range(2):
            result = await graph.ainvoke(
                {
                    "messages": [
                        HumanMessage(content=f"检索 {index}"),
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": f"call-{index}",
                                    "name": "recall_context",
                                    "args": {
                                        "terms": ["金额"],
                                        "resource_types": ["column", "metric", "value"],
                                    },
                                }
                            ],
                        ),
                    ]
                },
                config,
            )
        messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(len(messages), 2)
        original, latest = [json.loads(cast(str, m.content)) for m in messages]
        self.assertEqual(
            original["tables"]["orders"]["columns"]["amount"]["values"], ["100"]
        )
        self.assertIn("revenue", original["metrics"])
        self.assertEqual(latest["metrics"], {})
        self.assertNotIn("values", latest["tables"]["orders"]["columns"]["amount"])
        self.assertNotIn("rank_score", original["metrics"]["revenue"])
        self.assertEqual(runtime.search.await_count, 2)
        self.assertEqual(runtime.search.await_args.args[0], 7)
        saved = await graph.aget_state(config)
        self.assertEqual(saved.values["messages"], result["messages"])

    async def test_search_failure_returns_error(self):
        runtime = MagicMock(spec=SemanticRecallRuntime)
        runtime.search = AsyncMock(side_effect=RuntimeError("检索不可用"))
        result = await recall_context(
            {"configurable": {"user_id": 7}}, ["column"], ["金额"], 5, recall=runtime
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["details"][0]["msg"], "检索不可用")


class SemanticRecallRequestTest(unittest.TestCase):
    def test_resource_terms_require_nonempty_normalized_value(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticResourceRecallRequest(
                terms=["", "  "],
                resource_types=["column"],
            )


if __name__ == "__main__":
    unittest.main()
