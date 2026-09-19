"""任务资源初始化失败及同进程并发隔离测试。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager
from threading import Barrier
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.assistant import tasks as assistant_tasks
from app.assistant.conversations import resources as lifecycle_runtime
from app.metadata import tasks as metadata_tasks
from app.query import tasks as query_tasks
from app.workflows import tasks as workflow_tasks


@pytest.mark.parametrize("module", [assistant_tasks, workflow_tasks])
def test_lifecycle_task_cleans_up_after_sandbox_init_failure(module) -> None:
    persistence = MagicMock(init=AsyncMock(), close=AsyncMock())
    databases = []

    def postgres(*args):
        manager = MagicMock(close=AsyncMock())
        databases.append(manager)
        return manager

    sandbox = MagicMock(
        init=AsyncMock(side_effect=RuntimeError("sandbox init")),
        disconnect=AsyncMock(),
    )
    agents = MagicMock(close=AsyncMock(side_effect=RuntimeError("agents close")))

    @asynccontextmanager
    async def execution_lock(user_id):
        yield True

    state_store = MagicMock(
        execution_lock=execution_lock,
        extend_claim=AsyncMock(return_value=True),
        record_failure=AsyncMock(),
    )
    with (
        patch.object(
            workflow_tasks, "PostgresUserDeletionStateStore", return_value=state_store
        ),
        patch.object(
            lifecycle_runtime, "LangGraphPostgresManager", return_value=persistence
        ),
        patch.object(lifecycle_runtime, "PostgresClientManager", side_effect=postgres),
        patch.object(workflow_tasks, "PostgresClientManager", side_effect=postgres),
        patch.object(lifecycle_runtime, "create_sandbox_manager", return_value=sandbox),
        patch.object(lifecycle_runtime, "AgentManager", return_value=agents),
        patch.object(
            lifecycle_runtime,
            "build_conversation_lifecycle_service",
            return_value=MagicMock(),
        ),
        pytest.raises(RuntimeError, match="agents close"),
    ):
        if module is assistant_tasks:
            module.delete_conversation_resources_task(1, str(uuid4()))
        else:
            asyncio.run(module._process_user_deletion(1))
    persistence.close.assert_awaited_once()
    sandbox.disconnect.assert_awaited_once()
    for database in databases:
        database.close.assert_awaited_once()


@pytest.mark.parametrize("module", [metadata_tasks, query_tasks])
def test_index_task_resources_are_isolated_between_threads(module) -> None:
    barrier = Barrier(2)
    managers = []
    clients_seen = []

    def manager_factory(*args):
        owner_loop = asyncio.get_running_loop()
        manager = MagicMock()
        manager.session.return_value.__aenter__.return_value = MagicMock()
        manager.connection.return_value.__aenter__.return_value = MagicMock()

        async def close():
            assert asyncio.get_running_loop() is owner_loop

        manager.close = AsyncMock(side_effect=close)
        managers.append(manager)
        return manager

    async def operation(*args):
        # 元数据 operation 与查询 indexer 都接收 ES、Embedding 两个依赖。
        clients_seen.append(args[-2:])
        await asyncio.to_thread(barrier.wait, 5)
        return 1

    def indexer(*args):
        async def sync(*unused):
            return await operation(*args)

        return MagicMock(sync=sync)

    def worker(_):
        if module is metadata_tasks:
            return asyncio.run(module._run_with_metadata_resources(operation))
        return asyncio.run(module._sync_index(uuid4(), 1))

    with ExitStack() as stack:
        for name in (
            "PostgresClientManager",
            "ESClientManager",
            "EmbeddingClientManager",
        ):
            stack.enter_context(patch.object(module, name, side_effect=manager_factory))
        if module is metadata_tasks:
            stack.enter_context(
                patch.object(module, "DorisClientManager", side_effect=manager_factory)
            )
        else:
            stack.enter_context(
                patch.object(
                    module, "build_query_experience_indexer", side_effect=indexer
                )
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(worker, range(2))) == [1, 1]
    assert len(clients_seen) == 2
    assert clients_seen[0][0] is not clients_seen[1][0]
    assert clients_seen[0][1] is not clients_seen[1][1]
    for manager in managers:
        manager.init.assert_called_once()
        manager.close.assert_awaited_once()


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
        resources.auth_rate_limit.close.assert_called_once()

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
        for resource in (resources.persistence, resources.sandbox, resources.agents):
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
        ):
            resource.close = AsyncMock()
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

    with (
        patch.object(runtime, "_create_resources", side_effect=create),
        patch.object(runtime, "_verify_doris_query_identities", new=AsyncMock()),
    ):
        asyncio.run(run())
