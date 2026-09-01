"""DeepSeek Responses thinking 协议适配。"""

from typing import Any

from langchain_core.language_models import LangSmithParams, LanguageModelInput
from langchain_openai import ChatOpenAI


class DataAgentDeepSeekResponses(ChatOpenAI):
    """适配 DeepSeek 无状态 Responses thinking 续轮。"""

    def _get_ls_params(
        self,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LangSmithParams:
        """将 OpenAI 协议客户端调用归属到真实的 DeepSeek Provider。"""
        params = super()._get_ls_params(stop=stop, **kwargs)
        params["ls_provider"] = "deepseek"
        return params

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """保留 DeepSeek 下一次工具调用必须回传的明文 reasoning item。"""
        # LangChain 会在 store=false 时移除 OpenAI 无法无状态重放的明文
        # reasoning。DeepSeek 本身始终无状态，并要求客户端完整回传该 item，
        # 因此先按完整历史序列化，再把发往 Provider 的 store 恢复为 false。
        kwargs["store"] = True
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload["store"] = False
        return payload
