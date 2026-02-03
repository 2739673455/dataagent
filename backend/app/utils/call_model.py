from typing import Any

import httpx
from openai import AsyncOpenAI

# 全局共享的 httpx 连接池
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """获取全局共享的 httpx 客户端"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=120.0,  # 请求超时时间
            limits=httpx.Limits(
                max_keepalive_connections=20,  # 保持存活的空闲连接数
                max_connections=100,  # 最大连接数
                keepalive_expiry=30.0,  # 连接保持存活的时间（秒）
            ),
        )
    return _http_client


async def call_model(
    messages,
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict[str, Any] | None,
):
    """非流式调用模型"""
    client = AsyncOpenAI(
        base_url=base_url, api_key=api_key, http_client=_get_http_client()
    )

    completion = await client.chat.completions.create(
        messages=messages, model=model_name or "default", **(params or {})
    )

    return completion.choices[0].message.content


async def stream_model(
    messages,
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict[str, Any] | None,
):
    """流式调用模型"""
    client = AsyncOpenAI(
        base_url=base_url, api_key=api_key, http_client=_get_http_client()
    )

    stream = await client.chat.completions.create(
        messages=messages, model=model_name or "default", **(params or {}), stream=True
    )

    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
