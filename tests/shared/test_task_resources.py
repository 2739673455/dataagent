"""任务资源初始化失败及同进程并发隔离测试。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager, contextmanager
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
            lifecycle_runtime, "PostgresCheckpointStore", return_value=persistence
        ),
        patch.object(
            lifecycle_runtime,
            "PostgresAdvisoryLocks",
            return_value=MagicMock(init=AsyncMock(), close=AsyncMock()),
        ),
        patch.object(lifecycle_runtime, "PostgresClientManager", side_effect=postgres),
        patch.object(workflow_tasks, "PostgresClientManager", side_effect=postgres),
        patch.object(lifecycle_runtime, "DockerSandboxManager", return_value=sandbox),
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

    def manager_factory(*args, **kwargs):
        owner_loop = asyncio.get_running_loop()
        manager = MagicMock()
        manager.session.return_value.__aenter__.return_value = MagicMock()
        manager.engine.connect.return_value.__aenter__.return_value = MagicMock()

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
            "AsyncElasticsearch",
            "EmbeddingClient",
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
        manager.close.assert_awaited_once()


@contextmanager
def _web_dependencies(
    *, construct_failure=None, init_failure=None, close_failure=False
):
    """保留 Web 的真实装配与清理注册，只替换外部资源。"""
    from app import runtime

    created = []
    closed = []

    def factory(name):
        def create(*args, **kwargs):
            if name == construct_failure:
                raise RuntimeError("construct")
            owner_loop = asyncio.get_running_loop()
            resource = MagicMock()
            if name == "AgentManager":
                resource._runtime_factory = args[3]
            created.append((name, resource))

            async def close():
                assert asyncio.get_running_loop() is owner_loop
                closed.append(resource)
                if name == "AgentManager" and close_failure:
                    raise RuntimeError("shutdown")

            resource.close = AsyncMock(side_effect=close)
            resource.init_tables = AsyncMock()
            if name in {
                "PostgresCheckpointStore",
                "PostgresAdvisoryLocks",
                "DockerSandboxManager",
                "AgentManager",
            }:
                resource.init = AsyncMock(
                    side_effect=init_failure
                    if name == "PostgresCheckpointStore"
                    else None
                )
            if name == "AuthRateLimitService":
                resource.close = MagicMock(side_effect=lambda: closed.append(resource))
            return resource

        return create

    with ExitStack() as stack:
        for name in (
            "PostgresClientManager",
            "DorisClientManager",
            "DorisQueryClientRegistry",
            "EmbeddingClient",
            "AsyncElasticsearch",
            "PostgresCheckpointStore",
            "PostgresAdvisoryLocks",
            "DockerSandboxManager",
            "AgentManager",
            "ConversationRunService",
            "AuthRateLimitService",
        ):
            stack.enter_context(patch.object(runtime, name, side_effect=factory(name)))
        yield created, closed


@pytest.mark.parametrize("error", [RuntimeError("startup"), asyncio.CancelledError()])
def test_web_shutdown_attempts_every_resource_after_startup_failure(error) -> None:
    from app import runtime

    async def run():
        application = MagicMock()
        with pytest.raises(RuntimeError, match="shutdown"):
            async with runtime.lifespan(application):
                pytest.fail("启动失败不应进入业务阶段")
        assert closed == [resource for _, resource in reversed(created)]
        for name, resource in created:
            if name == "AuthRateLimitService":
                resource.close.assert_called_once()
            else:
                resource.close.assert_awaited_once()

    with _web_dependencies(init_failure=error, close_failure=True) as (created, closed):
        asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["EmbeddingClient", "AsyncElasticsearch", "AgentManager"]
)
def test_web_construction_failure_releases_preceding_resources(failure):
    from app import runtime

    async def run():
        with pytest.raises(RuntimeError, match="construct"):
            async with runtime.lifespan(MagicMock()):
                pytest.fail("构造失败不应进入业务阶段")
        assert created
        assert closed == [resource for _, resource in reversed(created)]

    with _web_dependencies(construct_failure=failure) as (created, closed):
        asyncio.run(run())


def test_web_applications_own_separate_resources_and_request_dependencies() -> None:
    import httpx
    from fastapi import FastAPI

    from app import runtime
    from app.dependencies import WebResourcesDep

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
                assert (
                    first_resources.agents._runtime_factory._recall.auth
                    is first_resources.auth
                )
                assert (
                    second_resources.agents._runtime_factory._recall.auth
                    is second_resources.auth
                )
            assert not hasattr(second.state, "resources")
            assert await read_owner(first) == id(first_resources.auth)
            first_resources.auth.close.assert_not_awaited()
            second_resources.auth.close.assert_awaited_once()
        assert not hasattr(first.state, "resources")
        first_resources.auth.close.assert_awaited_once()

    with _web_dependencies():
        asyncio.run(run())


@pytest.mark.parametrize("module", [metadata_tasks, query_tasks])
def test_index_task_constructor_failure_closes_existing_clients(module):
    embedding = MagicMock(close=AsyncMock())
    es = MagicMock(close=AsyncMock(side_effect=RuntimeError("close")))
    operation = AsyncMock()

    async def run():
        with pytest.raises(RuntimeError, match="close"):
            if module is metadata_tasks:
                await module._run_with_metadata_resources(operation)
            else:
                await module._sync_index(uuid4(), 1)
        embedding.close.assert_awaited_once()
        es.close.assert_awaited_once()
        operation.assert_not_awaited()

    with (
        patch.object(module, "EmbeddingClient", return_value=embedding),
        patch.object(module, "AsyncElasticsearch", return_value=es),
        patch.object(
            module, "PostgresClientManager", side_effect=RuntimeError("construct")
        ),
    ):
        asyncio.run(run())


def test_lifecycle_construction_failure_closes_previous_database():
    persistence = MagicMock(close=AsyncMock())
    postgres = MagicMock(close=AsyncMock())

    async def run():
        with pytest.raises(RuntimeError, match="construct"):
            async with lifecycle_runtime.conversation_lifecycle_resources():
                pytest.fail("构造失败不应进入业务阶段")
        postgres.close.assert_awaited_once()
        persistence.close.assert_awaited_once()

    with (
        patch.object(
            lifecycle_runtime,
            "PostgresAdvisoryLocks",
            return_value=MagicMock(close=AsyncMock()),
        ),
        patch.object(
            lifecycle_runtime, "PostgresCheckpointStore", return_value=persistence
        ),
        patch.object(
            lifecycle_runtime,
            "PostgresClientManager",
            side_effect=[postgres, RuntimeError("construct")],
        ),
    ):
        asyncio.run(run())
