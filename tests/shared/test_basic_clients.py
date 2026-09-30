"""基础客户端协议、真实资源释放及角色连接池更新回归。"""

import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from app.shared.clients import doris_client_manager as doris_module
from app.shared.clients import embedding_client as embedding_module
from app.shared.clients.doris_client_manager import (
    DorisClientManager,
    DorisQueryClientRegistry,
)
from app.shared.clients.embedding_client import EmbeddingClient
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import DBConfig, EmbeddingConfig
from app.shared.database.base import AuthBase


def _database_config():
    return DBConfig(
        host="localhost",
        port=9030,
        user="reader",
        password=SecretStr("test-only"),
        database="test",
    )


@pytest.mark.parametrize("api_key", [None, SecretStr("test-only")])
def test_embedding_orders_results_and_closes_http_client(api_key):
    requests = []
    http_clients = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        assert json.loads(request.content) == {
            "model": "test-model",
            "input": ["first", "second"],
        }
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers.get("Authorization") == (
            "Bearer test-only" if api_key else None
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2]},
                    {"index": 0, "embedding": [0.1]},
                ]
            },
        )

    def create(**kwargs):
        client = real_client(transport=httpx.MockTransport(respond), **kwargs)
        http_clients.append(client)
        return client

    async def run():
        client = EmbeddingClient(
            EmbeddingConfig(
                base_url="https://embedding.invalid/v1/",
                model="test-model",
                api_key=api_key,
                timeout=10,
                batch_size=32,
            )
        )
        try:
            assert await client.aembed_documents([]) == []
            assert not requests
            assert await client.aembed_documents(["first", "second"]) == [[0.1], [0.2]]
            assert requests[0].url.path == "/v1/embeddings"
        finally:
            await client.close()
        assert http_clients[0].is_closed

    with patch.object(embedding_module.httpx, "AsyncClient", side_effect=create):
        asyncio.run(run())


@pytest.mark.parametrize(
    "response,error",
    [
        (httpx.Response(200, json={"data": []}), ValueError),
        (httpx.Response(200, json={}), TypeError),
        (httpx.Response(503, json={"error": "unavailable"}), httpx.HTTPStatusError),
    ],
)
def test_embedding_propagates_response_errors(response, error):
    http_client = httpx.AsyncClient(
        base_url="https://embedding.invalid",
        transport=httpx.MockTransport(lambda request: response),
    )

    async def run():
        client = EmbeddingClient(
            EmbeddingConfig(
                base_url="https://embedding.invalid",
                model="test-model",
                api_key=None,
                timeout=10,
                batch_size=32,
            )
        )
        try:
            with pytest.raises(error):
                await client.aembed_documents(["first"])
        finally:
            await client.close()
        assert http_client.is_closed

    with patch.object(embedding_module.httpx, "AsyncClient", return_value=http_client):
        asyncio.run(run())


def test_databases_construct_and_release_without_network():
    async def run():
        postgres = PostgresClientManager(_database_config(), AuthBase)
        doris = DorisClientManager(_database_config())
        try:
            assert postgres.engine.url.drivername == "postgresql+psycopg"
            assert doris.engine.url.drivername == "mysql+asyncmy"
            # 实际 AsyncSession 关闭会执行 SQLAlchemy 的异步资源释放，需 greenlet。
            async with postgres.session() as session:
                assert session.bind is postgres.engine
                assert session.sync_session.expire_on_commit is False
        finally:
            await doris.close()
            await postgres.close()

    asyncio.run(run())


def test_query_registry_preserves_role_isolation_and_credential_updates():
    clients = []

    def create(config):
        client = DorisClientManager(config)
        client.close = AsyncMock(wraps=client.close)
        clients.append(client)
        return client

    async def run():
        registry = DorisQueryClientRegistry(_database_config())
        try:
            first = await registry.get_or_create("reader", "query_reader", "old")
            assert (
                await registry.get_or_create("reader", "query_reader", "old") is first
            )
            other = await registry.get_or_create("analyst", "query_reader", "old")
            assert other is not first
            replacement = await registry.get_or_create("reader", "query_reader", "new")
            assert replacement is not first
            cast(AsyncMock, first.close).assert_awaited_once()
            assert replacement.engine.url.password == "new"
            await registry.invalidate("reader")
            cast(AsyncMock, replacement.close).assert_awaited_once()
            assert (
                await registry.get_or_create("analyst", "query_reader", "old") is other
            )
        finally:
            await registry.close()
        for client in clients:
            client.close.assert_awaited_once()

    with patch.object(doris_module, "DorisClientManager", side_effect=create):
        asyncio.run(run())


def test_query_registry_failed_replacement_keeps_original_pool():
    async def run():
        registry = DorisQueryClientRegistry(_database_config())
        original = await registry.get_or_create("reader", "query_reader", "old")
        try:
            with (
                patch.object(
                    doris_module,
                    "DorisClientManager",
                    side_effect=RuntimeError("construct"),
                ),
                pytest.raises(RuntimeError, match="construct"),
            ):
                await registry.get_or_create("reader", "query_reader", "new")
            assert (
                await registry.get_or_create("reader", "query_reader", "old")
                is original
            )
        finally:
            await registry.close()

    asyncio.run(run())
