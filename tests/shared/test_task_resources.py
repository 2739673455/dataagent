"""任务资源初始化失败及同进程并发隔离测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_web_shutdown_attempts_every_resource_after_startup_failure() -> None:
    from app import runtime

    names = [
        "query_clients",
        "admin_doris",
        "auth",
        "meta",
        "assistant",
        "es",
        "embedding",
        "persistence",
        "sandbox",
        "agents",
        "runs",
        "tasks",
    ]
    managers = {name: MagicMock(close=AsyncMock()) for name in names}
    resources = MagicMock(**managers)
    managers["persistence"].init = AsyncMock(side_effect=RuntimeError("startup"))
    managers["agents"].close.side_effect = RuntimeError("shutdown")

    async def run() -> None:
        with pytest.raises(RuntimeError, match="shutdown"):
            async with runtime.lifespan(MagicMock()):
                pytest.fail("启动失败不应进入业务阶段")
        for manager in managers.values():
            manager.close.assert_awaited_once()

    with patch.object(runtime, "_create_resources", return_value=resources):
        asyncio.run(run())


def test_web_applications_own_separate_resources_and_request_dependencies() -> None:
    import httpx
    from fastapi import FastAPI

    from app import runtime
    from app.dependencies import WebResourcesDep

    created = []
    original_create = runtime._create_resources

    def create():
        # 保留真实依赖组装，仅替换联网初始化和关闭。
        resources = original_create()
        for resource in (
            resources.auth,
            resources.meta,
            resources.assistant,
            resources.admin_doris,
            resources.embedding,
            resources.es,
        ):
            resource.init = MagicMock()
        for resource in (resources.auth, resources.meta, resources.assistant):
            resource.init_tables = AsyncMock()
        for resource in (resources.persistence, resources.sandbox):
            resource.init = AsyncMock()
        for resource in (
            resources.auth,
            resources.meta,
            resources.assistant,
            resources.admin_doris,
            resources.query_clients,
            resources.embedding,
            resources.es,
            resources.persistence,
            resources.sandbox,
            resources.agents,
            resources.runs,
            resources.tasks,
        ):
            resource.close = AsyncMock()
        resources.tasks.start = MagicMock()
        created.append(resources)
        return resources

    def app():
        application = FastAPI()

        @application.get("/owner")
        def owner(resources: WebResourcesDep):
            return {"owner": id(resources.auth)}

        return application

    async def read_owner(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/owner")
            assert response.status_code == 200
            return response.json()["owner"]

    async def run():
        first, second = app(), app()
        async with runtime.lifespan(first):
            first_resources = first.state.resources
            async with runtime.lifespan(second):
                second_resources = second.state.resources
                assert await read_owner(first) == id(first_resources.auth)
                assert await read_owner(second) == id(second_resources.auth)
                for name in runtime.WebResources.__dataclass_fields__:
                    assert getattr(first_resources, name) is not getattr(
                        second_resources, name
                    )
                assert first_resources.recall.auth is first_resources.auth
                assert second_resources.recall.auth is second_resources.auth
            assert not hasattr(second.state, "resources")
            assert await read_owner(first) == id(first_resources.auth)
            first_resources.auth.close.assert_not_awaited()
            second_resources.auth.close.assert_awaited_once()
        assert not hasattr(first.state, "resources")
        first_resources.auth.close.assert_awaited_once()

    with patch.object(runtime, "_create_resources", side_effect=create):
        asyncio.run(run())
