"""专业 Agent 结构化输出策略。"""

from langchain.agents.structured_output import ProviderStrategy
from langchain_core.language_models import BaseChatModel

from app.assistant.agents.contracts import SpecialistResult


def specialist_response_format(
    model: BaseChatModel,
) -> type[SpecialistResult] | ProviderStrategy[SpecialistResult]:
    """按模型能力选择 Specialist 结构化输出策略。"""
    if model.profile and model.profile.get("structured_output"):
        # AutoStrategy 会选择 ProviderStrategy，但当前 LangChain 不会自动开启
        # Provider 的 strict 标志，因此这里显式声明以维持服务端结构约束。
        return ProviderStrategy(SpecialistResult, strict=True)
    return SpecialistResult
