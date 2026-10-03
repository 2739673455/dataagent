"""聊天服务依赖组装与三种 SSE 入口的 HTTP 回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.assistant.api import dependencies as runtime_dependencies
from app.assistant.api.chat.dependencies import _get_conversation_service
from app.assistant.api.chat.router import router
from app.assistant.contracts import ChatStreamDoneEvent
from app.assistant.errors import (
    ConversationNotFoundError,
    ConversationNotResumableError,
)
from app.dependencies import _get_current_user, _require_analysis_access
from app.shared.errors.exc_handlers import register_exception_handlers

_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


@pytest.mark.parametrize("field", ["analysis_id", "session_id"])
@pytest.mark.parametrize("value", ["INVALID", "a b", "a" * 65])
def test_session_history_rejects_invalid_identifiers_at_http_entry(field, value):
    app = FastAPI()
    app.include_router(router, prefix="/chat")
    service = MagicMock(delegation_messages=AsyncMock())

    async def current_user():
        return SimpleNamespace(id=12)

    async def conversations():
        return service

    app.dependency_overrides = {
        _get_current_user: current_user,
        _get_conversation_service: conversations,
    }
    identifiers = {"analysis_id": "sales", "session_id": "daily", field: value}

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(
                f"/chat/{_ID}/subagents/{identifiers['analysis_id']}/analyst/"
                f"{identifiers['session_id']}/runs/run-1/messages"
            )

    response = asyncio.run(request())
    assert response.status_code == 422
    assert not service.mock_calls


@pytest.mark.parametrize("entry", ["start", "resume", "subscribe"])
@pytest.mark.parametrize("failure", [False, True])
def test_stream_routes_preserve_frames_headers_and_business_errors(entry, failure):
    app = FastAPI()
    app.include_router(router, prefix="/chat")
    register_exception_handlers(app)
    repository = MagicMock(get=AsyncMock(return_value=None if failure else MagicMock()))
    runs = MagicMock()
    agents = MagicMock(
        read_planner_state=AsyncMock(return_value=SimpleNamespace(next_nodes=()))
    )
    lifecycle = MagicMock()
    closed = []

    async def events():
        try:
            yield ChatStreamDoneEvent(type="done")
        finally:
            closed.append(True)

    for method in ("start", "subscribe"):
        setattr(runs, method, AsyncMock(side_effect=lambda *args: events()))

    # 保留真实 ConversationTurnService 的依赖组装；仅替换其资源与方法行为。
    async def start(service, user_id, conversation_id, message):
        assert service._repository is repository
        assert service._state_reader is agents
        if failure:
            raise ConversationNotFoundError
        return await service._runs.start(user_id, conversation_id, message)

    async def resume(service, user_id, conversation_id):
        assert service._repository is repository
        if failure:
            raise ConversationNotResumableError
        return await service._runs.start(user_id, conversation_id, None)

    def dependency(value):
        async def resolve():
            return value

        return resolve

    app.dependency_overrides = {
        _require_analysis_access: dependency(SimpleNamespace(id=12)),
        runtime_dependencies._get_conversation_pg_repo: dependency(repository),
        runtime_dependencies._get_agent_state_reader: dependency(agents),
        runtime_dependencies._get_conversation_run_service: dependency(runs),
        runtime_dependencies._get_conversation_lifecycle_service: dependency(lifecycle),
    }

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            if entry == "start":
                return await client.post(
                    "/chat/stream",
                    json={
                        "conversation_id": str(_ID),
                        "message": {"parts": [{"type": "text", "text": "分析"}]},
                    },
                )
            if entry == "resume":
                return await client.post(f"/chat/{_ID}/resume")
            return await client.get(f"/chat/{_ID}/events")

    from unittest.mock import patch

    from app.assistant.conversations.turns import ConversationTurnService

    with (
        patch.object(ConversationTurnService, "start", start),
        patch.object(ConversationTurnService, "resume", resume),
    ):
        response = asyncio.run(request())
    if failure:
        assert response.status_code == (409 if entry == "resume" else 404)
        assert response.headers["content-type"].startswith("application/problem+json")
        assert not closed
    else:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.text == 'data: {"type":"done"}\n\n'
        assert closed == [True]
        if entry == "subscribe":
            runs.subscribe.assert_awaited_once_with(12, _ID)
        elif entry == "resume":
            runs.start.assert_awaited_once_with(12, _ID, None)
        else:
            assert runs.start.await_args.args[:2] == (12, _ID)
