"""用户选择接口及业务请求的身份绑定。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.identity.api.dependencies import CurrentUserDep
from app.identity.api.router import router
from app.identity.models.account import User
from app.shared.errors.exc_handlers import register_exception_handlers


def test_user_list_and_selected_identity():
    users = [
        User(id=1, username="admin", doris_role_name="dataagent_admin"),
        User(id=2, username="reader", doris_role_name="dataagent_reader"),
    ]
    closed = []

    @asynccontextmanager
    async def session():
        try:
            yield object()
        finally:
            closed.append(True)

    application = FastAPI()
    application.state.resources = SimpleNamespace(auth=SimpleNamespace(session=session))
    application.include_router(router, prefix="/api/v1/users")
    register_exception_handlers(application)

    @application.get("/selected")
    async def selected(user: CurrentUserDep):
        # 身份读取的连接在业务逻辑开始前已释放。
        assert closed
        return {"id": user.id, "role": user.doris_role_name}

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/users")
            assert response.json() == [
                {
                    "id": user.id,
                    "username": user.username,
                    "doris_role_name": user.doris_role_name,
                }
                for user in users
            ]
            assert (await client.get("/selected")).status_code == 401
            assert (
                await client.get("/selected", headers={"X-User-ID": "99"})
            ).status_code == 401
            for user in users:
                response = await client.get(
                    "/selected", headers={"X-User-ID": str(user.id)}
                )
                assert response.json() == {"id": user.id, "role": user.doris_role_name}
            for invalid in ("0", "-1", "admin"):
                assert (
                    await client.get("/selected", headers={"X-User-ID": invalid})
                ).status_code == 422
            assert (await client.post("/api/v1/users", json={})).status_code == 405

    with (
        patch(
            "app.identity.repositories.identity.IdentityPGRepo.list_users",
            new=AsyncMock(return_value=users),
        ),
        patch(
            "app.identity.repositories.identity.IdentityPGRepo.get_user_by_id",
            new=AsyncMock(
                side_effect=lambda uid: next((u for u in users if u.id == uid), None)
            ),
        ),
    ):
        asyncio.run(run())


def test_app_exposes_no_auth_or_management_routes():
    from main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/users" in paths
    assert not any(
        path.startswith(("/api/v1/auth", "/api/v1/admin", "/api/v1/tasks"))
        for path in paths
    )
    assert set(User.__table__.columns.keys()) == {"id", "username", "doris_role_name"}
