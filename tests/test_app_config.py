import unittest

from pydantic import ValidationError

from app.conf.app_config import Cfg, cfg


class AppConfigInvariantTest(unittest.TestCase):
    def test_query_database_must_match_metadata_database(self) -> None:
        values = cfg.model_dump(mode="python")
        values["doris_query"]["database"] = "other_database"

        with self.assertRaisesRegex(
            ValidationError,
            "doris_query.database must match the metadata Doris database",
        ):
            Cfg.model_validate(values)

    def test_planner_continuation_limit_must_not_be_negative(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["orchestration"]["max_continuations"] = -1

        with self.assertRaises(ValidationError):
            Cfg.model_validate(values)


if __name__ == "__main__":
    unittest.main()
