"""验证 Session 拆分后执行、删除与容量仍共享同一互斥边界。"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.assistant.execution.activity import SessionActivity
from app.assistant.models.session import AgentSessionKey
from app.assistant.sessions.control import SessionControl
from app.shared.errors.infrastructure import AdvisoryLockBusyError


def test_independent_services_share_lock_and_unpersisted_capacity():
    held = set()

    @asynccontextmanager
    async def lock(name):
        if name in held:
            raise AdvisoryLockBusyError("busy")
        held.add(name)
        try:
            yield
        finally:
            held.remove(name)

    conversation_id = uuid4()
    repo = MagicMock(list_namespaces=AsyncMock(return_value=[]))
    locks = MagicMock(advisory_lock=lock)
    execution = SessionControl(repo, locks, 7, conversation_id)
    deletion = SessionControl(repo, locks, 7, conversation_id)
    key = AgentSessionKey(7, conversation_id, "sales", "analyst", "one")
    other = AgentSessionKey(7, conversation_id, "sales", "analyst", "two")

    async def run():
        async with execution.lock(key), execution.reserve_capacity(key, 1):
            with pytest.raises(AdvisoryLockBusyError):
                async with deletion.lock(key):
                    pytest.fail("删除不能进入正在执行的 Session")
            with pytest.raises(RuntimeError, match="数量已达上限"):
                async with deletion.reserve_capacity(other, 1):
                    pytest.fail("未持久化的执行也占用容量")
        async with deletion.lock(key), deletion.reserve_capacity(other, 1):
            pass
        assert not held

    asyncio.run(run())


def test_capacity_does_not_retry_an_error_from_execution_body():
    attempts = []

    @asynccontextmanager
    async def lock(name):
        attempts.append(name)
        yield

    conversation_id = uuid4()
    control = SessionControl(
        MagicMock(list_namespaces=AsyncMock(return_value=[])),
        MagicMock(advisory_lock=lock),
        7,
        conversation_id,
    )
    key = AgentSessionKey(7, conversation_id, "sales", "analyst", "one")
    failure = AdvisoryLockBusyError("nested operation")

    async def run():
        with pytest.raises(AdvisoryLockBusyError) as raised:
            async with control.reserve_capacity(key, 3):
                raise failure
        assert raised.value is failure
        assert len(attempts) == 1

    asyncio.run(run())


def test_activity_is_scoped_and_cleared_when_execution_is_cancelled():
    activity = SessionActivity()
    conversation_id = uuid4()
    key = AgentSessionKey(7, conversation_id, "sales", "analyst", "one")
    with pytest.raises(asyncio.CancelledError), activity.track(key):
        assert activity.is_active(key)
        assert set(activity.snapshot(7, conversation_id)) == {key}
        assert activity.snapshot(8, conversation_id) == {}
        raise asyncio.CancelledError
    assert not activity.is_active(key)
