"""任务资源初始化失败及同进程并发隔离测试。"""

import asyncio
from contextlib import AsyncExitStack
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
        "checkpoint_pool",
        "sandbox",
        "agents",
        "runs",
        "tasks",
    ]
    closed = []
    managers = {
        name: MagicMock(
            close=AsyncMock(side_effect=lambda name=name: closed.append(name))
        )
        for name in names
    }
    resources = MagicMock(**managers)
    managers["checkpoint_pool"].open = AsyncMock(side_effect=RuntimeError("startup"))

    async def fail_close():
        closed.append("agents")
        raise RuntimeError("shutdown")

    managers["agents"].close.side_effect = fail_close

    async def run() -> None:
        with pytest.raises(RuntimeError, match="shutdown"):
            async with runtime.lifespan(MagicMock()):
                pytest.fail("启动失败不应进入业务阶段")
        assert closed == list(reversed(names))
        for manager in managers.values():
            manager.close.assert_awaited_once()

    def create(stack):
        for manager in managers.values():
            stack.push_async_callback(manager.close)
        return resources

    with patch.object(runtime, "_create_resources", side_effect=create):
        asyncio.run(run())


def test_web_applications_own_separate_resources_and_request_dependencies() -> None:
    import httpx
    from fastapi import FastAPI

    from app import runtime
    from app.dependencies import WebResourcesDep

    created = []
    original_create = runtime._create_resources

    def create(stack):
        # 保留真实依赖组装，仅替换联网初始化和关闭。
        construction_stack = AsyncExitStack()
        resources = original_create(construction_stack)
        construction_stack.pop_all()
        for resource in (resources.auth, resources.meta, resources.assistant):
            resource.init_tables = AsyncMock()
        resources.checkpoint_pool.open = AsyncMock()
        resources.checkpointer.setup = AsyncMock()
        for resource in (
            resources.sandbox,
            resources.agents,
        ):
            resource.init = AsyncMock()
        for resource in (
            resources.auth,
            resources.meta,
            resources.assistant,
            resources.admin_doris,
            resources.query_clients,
            resources.embedding,
            resources.es,
            resources.checkpoint_pool,
            resources.sandbox,
            resources.agents,
            resources.runs,
            resources.tasks,
        ):
            resource.close = AsyncMock(wraps=resource.close)
            stack.push_async_callback(resource.close)
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


def test_resource_construction_failure_closes_already_created_clients():
    from app import runtime

    postgres = [MagicMock(close=AsyncMock()) for _ in range(3)]
    doris = MagicMock(close=AsyncMock())
    registry = MagicMock(close=AsyncMock())
    embedding = MagicMock(close=AsyncMock())

    async def run():
        with pytest.raises(ValueError, match="invalid ES"):
            async with runtime.lifespan(MagicMock()):
                pytest.fail("construction must fail")
        for client in [*postgres, doris, registry, embedding]:
            client.close.assert_awaited_once()

    with (
        patch.object(runtime, "PostgresClientManager", side_effect=postgres),
        patch.object(runtime, "DorisClientManager", return_value=doris),
        patch.object(runtime, "DorisQueryClientRegistry", return_value=registry),
        patch.object(runtime, "EmbeddingClient", return_value=embedding),
        patch.object(
            runtime, "AsyncElasticsearch", side_effect=ValueError("invalid ES")
        ),
    ):
        asyncio.run(run())
