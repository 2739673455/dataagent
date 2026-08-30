"""DeepSeek thinking 工具调用协议适配。"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_deepseek import ChatDeepSeek


class DataAgentChatDeepSeek(ChatDeepSeek):
    """补齐 DeepSeek thinking 模式的工具续轮协议。"""

    def _thinking_enabled(self) -> bool:
        """判断请求是否启用了 DeepSeek thinking 模式。"""
        if not isinstance(self.extra_body, Mapping):
            return True
        thinking = self.extra_body.get("thinking")
        return not (
            isinstance(thinking, Mapping) and thinking.get("type") == "disabled"
        )

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """将历史 assistant reasoning_content 原样回传给 DeepSeek。"""
        source_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        request_messages = payload.get("messages")
        if not isinstance(request_messages, list):
            return payload

        for source, target in zip(source_messages, request_messages, strict=False):
            if not isinstance(source, AIMessage) or not isinstance(target, dict):
                continue
            reasoning_content = source.additional_kwargs.get("reasoning_content")
            if isinstance(reasoning_content, str):
                target["reasoning_content"] = reasoning_content
        return payload

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: dict[str, Any] | str | bool | None = None,
        strict: bool | None = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        """thinking 模式下移除 DeepSeek 不支持的强制工具选择。"""
        if self._thinking_enabled() and (
            isinstance(tool_choice, dict)
            or tool_choice is True
            or (isinstance(tool_choice, str) and tool_choice not in {"auto", "none"})
        ):
            tool_choice = None
        return super().bind_tools(
            tools,
            tool_choice=tool_choice,
            strict=strict,
            parallel_tool_calls=parallel_tool_calls,
            **kwargs,
        )
