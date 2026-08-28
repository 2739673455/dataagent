import unittest

from pydantic import ValidationError

from app.shared.config.app_config import Cfg, cfg


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


if __name__ == "__main__":
    unittest.main()
