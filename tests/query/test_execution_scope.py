"""查询执行归属独立于 Assistant 的专家枚举与检查点。"""

from uuid import uuid4

from app.query.contracts import QueryExecutionScope


def test_scope_accepts_non_agent_execution_owner():
    scope = QueryExecutionScope(7, uuid4(), "daily", "scheduler", "report")
    assert scope.agent_type == "scheduler"
