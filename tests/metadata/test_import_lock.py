"""导入租约互斥、续租失败取消和资源清理。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockNotOwnedError

from app.metadata.runtime import metadata_import_service


@pytest.fixture
def dependencies():
    events = []
    lock = MagicMock(
        acquire=AsyncMock(return_value=True),
        extend=AsyncMock(return_value=True),
        release=AsyncMock(side_effect=lambda: events.append("unlock")),
    )
    redis = MagicMock(lock=MagicMock(return_value=lock))
    redis.__aenter__ = AsyncMock(return_value=redis)
    redis.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()

    @asynccontextmanager
    async def session_context():
        yield session
        events.append("session_closed")

    @asynccontextmanager
    async def connection_context():
        yield MagicMock()

    postgres = MagicMock(
        session=MagicMock(side_effect=session_context),
        init_tables=AsyncMock(),
        close=AsyncMock(side_effect=lambda: events.append("postgres_closed")),
    )
    doris = MagicMock(engine=MagicMock(connect=connection_context), close=AsyncMock())
    es = MagicMock(close=AsyncMock())
    embedding = MagicMock(close=AsyncMock())
    service = MagicMock()
    with (
        patch("app.metadata.runtime.Redis.from_url", return_value=redis),
        patch("app.metadata.runtime.PostgresClientManager", return_value=postgres),
        patch("app.metadata.runtime.DorisClientManager", return_value=doris),
        patch("app.metadata.runtime.AsyncElasticsearch", return_value=es),
        patch("app.metadata.runtime.EmbeddingClient", return_value=embedding),
        patch("app.metadata.runtime.build_meta_index_service", return_value=service),
    ):
        yield lock, redis, [postgres, doris, es, embedding], service, events, session


def test_conflict_does_not_initialize_database_or_release_other_lock(dependencies):
    lock, redis, _, _, _, _ = dependencies
    lock.acquire.return_value = False

    async def run():
        with pytest.raises(RuntimeError, match="已有元数据导入脚本"):
            async with metadata_import_service():
                pytest.fail("must not enter")

    asyncio.run(run())
    from app.metadata import runtime

    for constructor in (
        runtime.PostgresClientManager,
        runtime.DorisClientManager,
        runtime.AsyncElasticsearch,
        runtime.EmbeddingClient,
    ):
        constructor.assert_not_called()
    lock.release.assert_not_awaited()
    redis.__aexit__.assert_awaited_once()
    assert redis.lock.call_args.kwargs["blocking"] is False


def test_success_renews_and_closes_resources_before_unlock(dependencies):
    lock, redis, managers, service, events, session = dependencies

    async def run():
        renewed = asyncio.Event()

        async def extend(*args, **kwargs):
            renewed.set()
            return True

        lock.extend.side_effect = extend
        with patch("app.metadata.runtime._LOCK_RENEW_SECONDS", 0):
            async with metadata_import_service() as actual:
                assert actual is service
                await asyncio.wait_for(renewed.wait(), 1)
        calls = lock.extend.await_count
        await asyncio.sleep(0)
        assert lock.extend.await_count == calls

    asyncio.run(run())
    lock.extend.assert_awaited_with(60, replace_ttl=True)
    lock.release.assert_awaited_once()
    for manager in managers:
        manager.close.assert_awaited_once()
    assert events.index("postgres_closed") < events.index("unlock")
    managers[0].session.assert_called_once()
    session.scalar.assert_not_called()
    redis.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("failure", [LockNotOwnedError, RedisConnectionError])
def test_lost_lease_cancels_import_before_it_can_continue(dependencies, failure):
    lock, _, managers, _, _, _ = dependencies
    lock.extend.side_effect = failure("lease lost")
    lock.release.side_effect = LockNotOwnedError("different owner")
    cancelled = []

    async def run():
        with patch("app.metadata.runtime._LOCK_RENEW_SECONDS", 0):
            with pytest.raises(ExceptionGroup) as exc:
                async with metadata_import_service():
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.append(True)
                        raise
                    pytest.fail("import must not continue after losing its lease")
            assert any(isinstance(e, failure) for e in exc.value.exceptions)

    asyncio.run(asyncio.wait_for(run(), 1))
    assert cancelled == [True]
    lock.release.assert_awaited_once()
    for manager in managers:
        manager.close.assert_awaited_once()


def test_import_failure_releases_lock_and_resources(dependencies):
    lock, _, managers, _, _, _ = dependencies

    async def run():
        with pytest.raises(ExceptionGroup) as exc:
            async with metadata_import_service():
                raise ValueError("index failed")
        assert any(isinstance(e, ValueError) for e in exc.value.exceptions)

    asyncio.run(run())
    lock.release.assert_awaited_once()
    for manager in managers:
        manager.close.assert_awaited_once()


def test_cancellation_releases_lock_and_resources(dependencies):
    lock, _, managers, _, _, _ = dependencies

    async def run():
        entered = asyncio.Event()

        async def importing():
            async with metadata_import_service():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(importing())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    lock.release.assert_awaited_once()
    for manager in managers:
        manager.close.assert_awaited_once()
