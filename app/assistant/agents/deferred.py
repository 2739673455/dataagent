"""图编译时声明模型和工具，执行时解析已初始化的资源。"""

from collections.abc import Callable
from typing import Any, cast

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult


class DeferredChatModel(BaseChatModel):
    """保留模型能力声明，将模型调用交给当前运行时的真实客户端。"""

    resolve: Callable[[], BaseChatModel]

    @property
    def _llm_type(self) -> str:
        return "dataagent-deferred"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        result = self.resolve().generate([messages], stop=stop, **kwargs)
        return ChatResult(
            generations=cast(list[ChatGeneration], result.generations[0]),
            llm_output=result.llm_output,
        )

    def bind_tools(self, tools, **kwargs):
        return self.resolve().bind_tools(tools, **kwargs)

    async def ainvoke(self, input, config=None, *, stop=None, **kwargs):
        return await self.resolve().ainvoke(input, config, stop=stop, **kwargs)

    async def astream(self, input, config=None, *, stop=None, **kwargs):
        async for chunk in self.resolve().astream(input, config, stop=stop, **kwargs):
            yield chunk


register_harness_profile(
    "deferredchatmodel",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
    ),
)


class DynamicToolsMiddleware(AgentMiddleware[Any, Any, Any]):
    """在模型与工具调用时接入初始化后的 MCP 工具。"""

    def __init__(self, tools: Callable[[], list]):
        self._tools = tools

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=[*request.tools, *self._tools()]))

    async def awrap_tool_call(self, request, handler):
        tool = next(
            (t for t in self._tools() if t.name == request.tool_call["name"]), None
        )
        return await handler(
            request.override(tool=tool) if tool is not None else request
        )
