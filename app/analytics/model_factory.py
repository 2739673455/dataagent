"""语言模型实例构建"""

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.shared.config import app_config


def create_configured_model(model_name: str) -> BaseChatModel:
    """按配置名称创建聊天模型"""
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
    return init_chat_model(
        model_provider=model_cfg.model_provider,
        model=model_cfg.model,
        base_url=model_cfg.base_url,
        api_key=model_cfg.api_key,
        profile=model_cfg.profile,
        request_timeout=30,
        max_retries=3,
        **model_cfg.params,
    )
