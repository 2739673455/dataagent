"""聊天服务依赖组装与三种 SSE 入口的 HTTP 回归测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.assistant.api import dependencies as runtime_dependencies
from app.assistant.api.chat import dependencies as chat_dependencies
from app.assistant.api.chat.router import router
from app.assistant.events.schemas import ChatStreamDoneEvent
from app.assistant.execution.planner import PlannerTurnNotResumableError
from app.assistant.execution.turn import ConversationMissingError
from app.identity.api.auth.dependencies import _require_analysis_access
from app.shared.errors.exc_handlers import register_exception_handlers

_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


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

    for method in ("start_turn", "resume_turn", "subscribe"):
        setattr(runs, method, AsyncMock(side_effect=lambda *args: events()))

    # 保留真实 ConversationTurnService 的依赖组装；仅替换其资源与方法行为。
    async def start(service, user_id, conversation_id, message):
        assert service._repository is repository
        assert service._lifecycle is lifecycle
        assert service._agents is agents
        if failure:
            raise ConversationMissingError
        return await service._runs.start_turn(user_id, conversation_id, message)

    async def resume(service, user_id, conversation_id):
        assert service._repository is repository
        if failure:
            raise PlannerTurnNotResumableError
        return await service._runs.resume_turn(user_id, conversation_id)

    def dependency(value):
        async def resolve():
            return value

        return resolve

    app.dependency_overrides = {
        _require_analysis_access: dependency(SimpleNamespace(id=12)),
        chat_dependencies._get_conversation_pg_repo: dependency(repository),
        runtime_dependencies._get_agent_manager: dependency(agents),
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

    from app.assistant.execution.turn import ConversationTurnService

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
            runs.resume_turn.assert_awaited_once_with(12, _ID)
        else:
            assert runs.start_turn.await_args.args[:2] == (12, _ID)
