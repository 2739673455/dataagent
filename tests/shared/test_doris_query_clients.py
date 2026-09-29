"""预定义查询身份的连接池复用与关闭。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.shared.clients.doris_client_manager import DorisQueryClientRegistry
from app.shared.config.app_config import cfg


def test_role_pools_are_isolated_and_credentials_stay_fixed_until_restart():
    first, second = [MagicMock(close=AsyncMock()) for _ in range(2)]
    registry = DorisQueryClientRegistry(cfg.doris)
    with patch(
        "app.shared.clients.doris_client_manager.DorisClientManager",
        side_effect=[first, second],
    ) as create:
        assert registry.get_or_create("reader", "query_reader", "old") is first
        assert registry.get_or_create("reader", "changed", "new") is first
        assert registry.get_or_create("admin", "query_admin", "admin") is second
    assert create.call_count == 2
    configs = [call.args[0] for call in create.call_args_list]
    assert [(item.user, item.password.get_secret_value()) for item in configs] == [
        ("query_reader", "old"),
        ("query_admin", "admin"),
    ]
    assert all(item.host == cfg.doris.host for item in configs)
    asyncio.run(registry.close())
    for client in (first, second):
        client.close.assert_awaited_once()


def test_close_attempts_all_pools_even_when_one_fails():
    first = MagicMock(close=AsyncMock())
    second = MagicMock(close=AsyncMock(side_effect=RuntimeError("close failed")))
    registry = DorisQueryClientRegistry(cfg.doris)
    registry._clients = {"reader": first, "admin": second}
    with pytest.raises(RuntimeError, match="close failed"):
        asyncio.run(registry.close())
    for client in (first, second):
        client.close.assert_awaited_once()
    assert not registry._clients
