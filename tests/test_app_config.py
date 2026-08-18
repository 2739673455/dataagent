import unittest

from pydantic import ValidationError

from app.conf.app_config import Cfg, cfg


class AppConfigInvariantTest(unittest.TestCase):
    def test_exactly_one_default_doris_role_is_required(self) -> None:
        values = cfg.model_dump(mode="python")
        for role in values["doris_roles"].values():
            role["is_default"] = False

        with self.assertRaisesRegex(
            ValidationError,
            "exactly one default Doris role is required",
        ):
            Cfg.model_validate(values)

    def test_doris_role_query_users_must_be_unique(self) -> None:
        values = cfg.model_dump(mode="python")
        roles = list(values["doris_roles"].values())
        roles[1]["query_user"] = roles[0]["query_user"]

        with self.assertRaisesRegex(
            ValidationError,
            "Doris role query users must be unique",
        ):
            Cfg.model_validate(values)

    def test_security_admin_must_target_metadata_database(self) -> None:
        values = cfg.model_dump(mode="python")
        values["doris_security_admin"]["database"] = "other"

        with self.assertRaisesRegex(
            ValidationError,
            "Doris security admin must target the metadata database",
        ):
            Cfg.model_validate(values)

    def test_planner_continuation_limit_must_not_be_negative(self) -> None:
        values = cfg.model_dump(mode="python")
        values["agent"]["orchestration"]["max_continuations"] = -1

        with self.assertRaises(ValidationError):
            Cfg.model_validate(values)


if __name__ == "__main__":
    unittest.main()
