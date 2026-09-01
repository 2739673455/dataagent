"""语言模型工厂测试。"""

from unittest.mock import patch

from pydantic import SecretStr

from app.assistant.model_factory import create_configured_model
from app.shared.config.app_config import ModelCfg, ModelProfileCfg, cfg


def _model_config(
    *,
    provider: str,
    protocol: str,
    params: dict[str, object] | None = None,
    structured_output: bool = False,
) -> ModelCfg:
    return ModelCfg.model_validate(
        {
            "model_provider": provider,
            "api_protocol": protocol,
            "model": "provider/test-model",
            "base_url": "https://models.example.test/v1",
            "api_key": SecretStr("test-api-key"),
            "params": params or {},
            "profile": ModelProfileCfg(
                image_inputs=True,
                structured_output=structured_output,
                max_input_tokens=123_456,
            ),
        }
    )


def test_model_factory_configures_responses_without_provider_storage() -> None:
    model_name = "test-responses-model"
    model_cfg = _model_config(provider="deepseek", protocol="responses")
    with (
        patch.dict(cfg.lm_config.models, {model_name: model_cfg}),
        patch("app.assistant.model_factory.DataAgentDeepSeekResponses") as model_class,
    ):
        create_configured_model(model_name)

    kwargs = model_class.call_args.kwargs
    api_key = kwargs["api_key"]
    assert isinstance(api_key, str)
    assert kwargs["use_responses_api"] is True
    assert kwargs["output_version"] == "responses/v1"
    assert kwargs["store"] is False
    assert kwargs["use_previous_response_id"] is False
    assert kwargs["timeout"] == 30
    assert kwargs["max_retries"] == 0
    assert kwargs["profile"]["image_inputs"] is True
    assert kwargs["profile"]["image_tool_message"] is True


def test_model_factory_uses_openrouter_provider_client() -> None:
    model_name = "test-openrouter-model"
    model_cfg = _model_config(
        provider="openrouter",
        protocol="chat_completions",
        params={"reasoning": {"effort": "medium"}},
        structured_output=True,
    )
    with (
        patch.dict(cfg.lm_config.models, {model_name: model_cfg}),
        patch("app.assistant.model_factory.ChatOpenRouter") as model_class,
    ):
        create_configured_model(model_name)

    kwargs = model_class.call_args.kwargs
    assert "model_provider" not in kwargs
    assert "use_responses_api" not in kwargs
    assert kwargs["model"] == "provider/test-model"
    assert kwargs["profile"]["image_inputs"] is True
    assert kwargs["profile"]["structured_output"] is True
    assert kwargs["profile"]["max_input_tokens"] == 123_456
    assert kwargs["profile"]["image_tool_message"] is False
    assert kwargs["reasoning"] == {"effort": "medium"}
    assert kwargs["timeout"] == 30_000
    assert kwargs["max_retries"] == 0
