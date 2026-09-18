"""任务资源初始化失败及同进程并发隔离测试。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.assistant import lifecycle_runtime
from app.assistant import tasks as assistant_tasks
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

    with (
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

    resources = [
        "query_doris_client_registry",
        "admin_doris_client_manager",
        "auth_postgres_client_manager",
        "meta_postgres_client_manager",
        "assistant_postgres_client_manager",
        "es_client_manager",
        "embedding_client_manager",
        "langgraph_postgres_manager",
        "sandbox_manager",
        "agent_manager",
        "conversation_run_service",
    ]
    managers = {name: MagicMock(close=AsyncMock()) for name in resources}
    managers["langgraph_postgres_manager"].init = AsyncMock(
        side_effect=RuntimeError("startup")
    )
    managers["agent_manager"].close.side_effect = RuntimeError("shutdown")

    async def run() -> None:
        with pytest.raises(RuntimeError, match="shutdown"):
            async with runtime.lifespan(MagicMock()):
                pytest.fail("启动失败不应进入业务阶段")
        for manager in managers.values():
            manager.close.assert_awaited_once()

    with patch.multiple(runtime, **managers):
        asyncio.run(run())
