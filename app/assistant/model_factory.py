"""语言模型实例构建。"""

from typing import cast

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel, ModelProfile
from langchain_openai import ChatOpenAI

from app.assistant.deepseek_model import (
    DataAgentChatDeepSeek,
    DataAgentDeepSeekResponses,
)
from app.shared.config import app_config


def create_configured_model(model_name: str) -> BaseChatModel:
    """按配置名称创建聊天模型。"""
    try:
        model_cfg = app_config.cfg.lm_config.models[model_name]
    except KeyError as exc:
        raise ValueError(f"未知的语言模型配置: {model_name}") from exc
    register_harness_profile(
        f"{model_cfg.model_provider}:{model_cfg.model}",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    profile = cast(
        ModelProfile,
        {
            **model_cfg.profile.model_dump(),
            "image_tool_message": model_cfg.api_protocol == "responses"
            and model_cfg.profile.image_inputs,
        },
    )
    model_kwargs = {
        **model_cfg.params,
        "model": model_cfg.model,
        "base_url": model_cfg.base_url,
        "api_key": model_cfg.api_key.get_secret_value(),
        "profile": profile,
        "request_timeout": 30,
        "max_retries": 3,
        "streaming": True,
    }
    if model_cfg.api_protocol == "responses":
        model_class = (
            DataAgentDeepSeekResponses
            if model_cfg.model_provider == "deepseek"
            else ChatOpenAI
        )
        return model_class(
            **model_kwargs,
            use_responses_api=True,
            output_version="responses/v1",
            store=False,
            use_previous_response_id=False,
        )
    if model_cfg.model_provider == "deepseek":
        return DataAgentChatDeepSeek(**model_kwargs)
    return init_chat_model(
        model_provider=model_cfg.model_provider,
        **model_kwargs,
    )
