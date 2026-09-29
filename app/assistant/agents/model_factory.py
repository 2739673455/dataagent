"""Chat Completions 模型构建与 DeepSeek 思考内容回传。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from app.shared.config import app_config

_REQUEST_TIMEOUT_SECONDS = 30


class DataAgentDeepSeek(ChatDeepSeek):
    """工具调用后将思考内容随历史消息回传给 DeepSeek。"""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        for message, item in zip(messages, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                item["reasoning_content"] = message.additional_kwargs.get(
                    "reasoning_content", ""
                )
        return payload


@asynccontextmanager
async def create_configured_model(model_name: str) -> AsyncGenerator[BaseChatModel]:
    """创建并持有聊天模型客户端，退出时在当前循环中关闭。"""
    try:
        model_cfg = app_config.cfg.lm_config.models[model_name]
    except KeyError as exc:
        raise ValueError(f"未知的语言模型配置: {model_name}") from exc
    model_kwargs = {
        **model_cfg.params,
        "model": model_cfg.model,
        "base_url": model_cfg.base_url,
        "api_key": model_cfg.api_key.get_secret_value(),
        "profile": model_cfg.profile.model_dump(),
        "max_retries": 0,
        "streaming": True,
    }
    model_class = (
        DataAgentDeepSeek if model_cfg.model_provider == "deepseek" else ChatOpenAI
    )
    with httpx.Client(
        timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=True
    ) as http_client:
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        ) as http_async_client:
            model_kwargs.update(
                http_client=http_client,
                http_async_client=http_async_client,
            )
            yield model_class(
                **model_kwargs,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                use_responses_api=False,
            )
