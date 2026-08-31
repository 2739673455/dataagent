import unittest
from datetime import timedelta

from pydantic import ValidationError

from app.shared.config.app_config import Cfg, StreamableHttpMCPCfg, cfg


class AppConfigInvariantTest(unittest.TestCase):
    def test_doris_credential_encryption_key_is_required(self) -> None:
        values = cfg.model_dump(mode="python")
        values["doris_credentials"]["encryption_key"] = "short"

        with self.assertRaises(ValidationError):
            Cfg.model_validate(values)

    def test_planner_continuation_limit_must_not_be_negative(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["orchestration"]["max_continuations"] = -1

        with self.assertRaises(ValidationError):
            Cfg.model_validate(values)

    def test_runtime_secrets_are_redacted_from_config_serialization(self) -> None:
        serialized = cfg.model_dump_json()
        tavily = cfg.mcp["tavily"]
        self.assertIsInstance(tavily, StreamableHttpMCPCfg)
        assert isinstance(tavily, StreamableHttpMCPCfg)

        for secret in (
            cfg.auth.jwt_secret,
            cfg.doris_credentials.encryption_key,
            cfg.doris.password,
            cfg.auth_postgresql.password,
            cfg.meta_postgresql.password,
            cfg.langgraph_postgresql.password,
            cfg.task_queue.broker_url,
            cfg.task_queue.result_backend,
            cfg.sandbox.ownership.redis_url,
            cfg.embedding.api_key,
            *(model.api_key for model in cfg.lm_config.models.values()),
            tavily.url,
        ):
            if secret is not None and secret.get_secret_value() in serialized:
                self.fail("应用配置序列化泄露了密钥字段")

    def test_active_language_model_must_exist(self) -> None:
        values = cfg.model_dump(mode="python")
        values["lm_config"]["active"] = "missing-model"

        with self.assertRaisesRegex(ValidationError, "active 引用了未知模型"):
            Cfg.model_validate(values)

    def test_every_specialist_requires_an_explicit_configuration(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["specialists"].pop("reviewer")

        with self.assertRaisesRegex(ValidationError, "缺少配置: reviewer"):
            Cfg.model_validate(values)

    def test_unknown_specialist_configuration_is_rejected(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["specialists"]["unknown_agent"] = {"model": "default"}

        with self.assertRaisesRegex(ValidationError, "unknown_agent"):
            Cfg.model_validate(values)

    def test_specialist_model_must_exist(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["specialists"]["analyst"]["model"] = "missing-model"

        with self.assertRaisesRegex(ValidationError, "引用了未知模型"):
            Cfg.model_validate(values)

    def test_unknown_top_level_configuration_is_rejected(self) -> None:
        values = cfg.model_dump(mode="python")
        values["unknown_section"] = {}

        with self.assertRaisesRegex(ValidationError, "unknown_section"):
            Cfg.model_validate(values)

    def test_unknown_nested_configuration_is_rejected(self) -> None:
        values = cfg.model_dump(mode="python")
        values["query"]["max_result_rows"] = 1000

        with self.assertRaisesRegex(ValidationError, "max_result_rows"):
            Cfg.model_validate(values)

    def test_mcp_timeout_must_be_positive(self) -> None:
        values = cfg.model_dump(mode="python")
        values["mcp"]["tavily"]["timeout"] = timedelta(seconds=-1)

        with self.assertRaises(ValidationError):
            Cfg.model_validate(values)


if __name__ == "__main__":
    unittest.main()
