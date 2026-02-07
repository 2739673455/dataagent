from typing import Any

from openai import AsyncOpenAI

from .http_client import get_http_client


async def call_model(
    messages,
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict[str, Any] | None,
):
    """非流式调用模型"""
    client = AsyncOpenAI(
        base_url=base_url, api_key=api_key, http_client=get_http_client()
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
        base_url=base_url, api_key=api_key, http_client=get_http_client()
    )

    stream = await client.chat.completions.create(
        messages=messages, model=model_name or "default", **(params or {}), stream=True
    )

    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
