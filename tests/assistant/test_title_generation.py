"""标题模型调用不持有数据库会话，更新仍受归属和版本条件保护。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.dialects import postgresql

from app.assistant import tasks
from app.assistant.services.title import ConversationTitleService


@pytest.mark.parametrize("concurrent_change", [None, "rename", "delete"])
def test_title_wait_does_not_open_database_and_update_keeps_conditions(
    concurrent_change,
):
    events = []
    conversation_id = uuid4()
    current_title = "即时标题"
    deleting = False

    @asynccontextmanager
    async def model_scope(_):
        events.append("model-open")
        try:
            yield model
        finally:
            events.append("model-close")

    async def generate(_):
        nonlocal current_title, deleting
        postgres_factory.assert_not_called()
        events.append("model-wait")
        await asyncio.sleep(0)
        if concurrent_change == "rename":
            current_title = "手动标题"
        elif concurrent_change == "delete":
            deleting = True
        return AIMessage(content="标题：“订单分析”")

    async def execute(statement):
        assert events == ["model-open", "model-wait", "model-close", "database-open"]
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "conversations.user_id =" in sql
        assert "conversations.id =" in sql
        assert "conversations.title =" in sql
        assert "conversations.deletion_requested_at IS NULL" in sql
        assert 7 in compiled.params.values()
        assert conversation_id in compiled.params.values()
        assert "即时标题" in compiled.params.values()
        assert "订单分析" in compiled.params.values()
        return SimpleNamespace(
            rowcount=int(current_title == "即时标题" and not deleting)
        )

    model = MagicMock(ainvoke=AsyncMock(side_effect=generate))
    session = MagicMock(execute=AsyncMock(side_effect=execute), flush=AsyncMock())
    postgres = MagicMock(
        close=AsyncMock(side_effect=lambda: events.append("database-close"))
    )
    postgres.session.return_value.__aenter__.return_value = session

    def create(*_):
        events.append("database-open")
        return postgres

    with (
        patch.object(tasks, "create_configured_model", model_scope),
        patch.object(
            tasks, "PostgresClientManager", side_effect=create
        ) as postgres_factory,
    ):
        updated = asyncio.run(
            tasks._generate_conversation_title(
                7, conversation_id, "即时标题", "分析订单"
            )
        )
    assert updated is (concurrent_change is None)
    session.begin.return_value.__aexit__.assert_awaited_once_with(None, None, None)
    assert events[-1] == "database-close"


@pytest.mark.parametrize(
    "result", [" \n\t ", RuntimeError("model down"), asyncio.CancelledError()]
)
def test_empty_failed_or_cancelled_model_does_not_create_database(result):
    closed = []
    model = MagicMock(
        ainvoke=AsyncMock(
            return_value=AIMessage(content=result) if isinstance(result, str) else None
        )
    )
    if not isinstance(result, str):
        model.ainvoke.side_effect = result

    @asynccontextmanager
    async def model_scope(_):
        try:
            yield model
        finally:
            closed.append(True)

    async def run():
        operation = tasks._generate_conversation_title(7, uuid4(), "即时标题", "输入")
        if isinstance(result, str):
            assert await operation is False
        else:
            with pytest.raises(type(result)):
                await operation

    with (
        patch.object(tasks, "create_configured_model", model_scope),
        patch.object(tasks, "PostgresClientManager") as postgres,
    ):
        asyncio.run(run())
    postgres.assert_not_called()
    assert closed == [True]


def test_title_normalization_and_model_input_limit_are_preserved():
    model = MagicMock(
        ainvoke=AsyncMock(return_value=AIMessage(content="《" + "标题" * 50 + "》"))
    )
    title = asyncio.run(ConversationTitleService(model).generate("x" * 5000))
    assert title == "标题" * 32
    assert len(model.ainvoke.call_args.args[0][1].content) == 4000
