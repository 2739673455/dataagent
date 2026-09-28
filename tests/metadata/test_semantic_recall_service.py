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
from pydantic import BaseModel, ValidationError

from app.assistant.agents.explorer.tools import create_semantic_recall_tool
from app.assistant.agents.explorer.tools.semantic_recall import _recall_context
from app.metadata.models.search import (
    SemanticColumnRecallResult,
    SemanticMetricRecallResult,
    SemanticResourceRecallRequest,
    SemanticResourceRecallResponse,
    SemanticTableContext,
    SemanticValueRecallResult,
)
from app.metadata.services.recall_handler import SemanticRecallHandler


def build_response(
    query: str,
    *,
    score: float,
    reason: str,
) -> SemanticResourceRecallResponse:
    """构造包含重复资源的测试召回响应。"""
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
            )
        ],
        values=[
            SemanticValueRecallResult(
                value="paid",
                t_name="orders",
                c_name="status",
                rank_score=score,
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
        runtime = MagicMock(spec=SemanticRecallHandler)
        runtime.search = AsyncMock(side_effect=[first, second])
        tool = create_semantic_recall_tool(runtime)
        self.assertEqual(tool.name, "recall_context")
        self.assertNotIn("query", tool.args)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([tool]))
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

    async def test_tool_schema_validates_and_normalizes_requests(self):
        handler = MagicMock(spec=SemanticRecallHandler)
        handler.search = AsyncMock(
            return_value=build_response("金额", score=0.8, reason="金额")
        )
        tool = create_semantic_recall_tool(handler)
        schema = cast(type[BaseModel], tool.tool_call_schema).model_json_schema()
        self.assertNotIn("runtime", schema["properties"])
        self.assertEqual(schema["properties"]["terms"]["maxItems"], 50)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([tool]))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        for terms, limit in (([" "], 5), (["金额"], 21), (["金额"] * 51, 5)):
            result = await graph.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "invalid",
                                    "name": "recall_context",
                                    "args": {
                                        "terms": terms,
                                        "resource_types": ["column"],
                                        "limit_per_type": limit,
                                    },
                                }
                            ],
                        )
                    ]
                }
            )
            self.assertEqual(result["messages"][-1].status, "error")
        handler.search.assert_not_awaited()
        result = await graph.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "valid",
                                "name": "recall_context",
                                "args": {
                                    "terms": [" 金额 ", "金额"],
                                    "resource_types": ["column", "column"],
                                    "extra": "ignored",
                                },
                            }
                        ],
                    )
                ]
            },
            {"configurable": {"user_id": 7}},
        )
        self.assertEqual(result["messages"][-1].status, "success")
        user_id, request = handler.search.await_args.args
        self.assertEqual(user_id, 7)
        self.assertEqual(request.terms, ["金额"])
        self.assertEqual(request.resource_types, ["column"])
        self.assertEqual(request.limit_per_type, 5)

    async def test_search_failure_returns_error(self):
        runtime = MagicMock(spec=SemanticRecallHandler)
        runtime.search = AsyncMock(side_effect=RuntimeError("检索不可用"))
        result = await _recall_context(
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
