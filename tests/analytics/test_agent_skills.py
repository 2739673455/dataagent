import unittest
from pathlib import Path
from typing import Any, cast

from deepagents.backends import StateBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage

from app.analytics.agents.analyst import agent as analyst_agent
from app.analytics.agents.skills import (
    agent_skills_mount_path,
    mount_agent_skills,
    packaged_agent_skill_mounts,
)

_ANALYST_SKILLS_PATH = agent_skills_mount_path("analyst")


class AgentSkillsTest(unittest.TestCase):
    def test_analysis_skill_is_loaded_from_analyst_directory(self) -> None:
        skill_directory = Path(analyst_agent.__file__).with_name("skills")
        backend, _ = mount_agent_skills(
            StateBackend(),
            skill_directory,
            [_ANALYST_SKILLS_PATH],
        )
        middleware = SkillsMiddleware(
            backend=backend,
            sources=[_ANALYST_SKILLS_PATH],
        )

        update = middleware.before_agent(
            cast(Any, {}),
            cast(Any, None),
            cast(Any, {}),
        )

        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(
            [skill["name"] for skill in update["skills_metadata"]],
            ["analysis"],
        )

    def test_agent_cannot_modify_mounted_skill(self) -> None:
        skill_directory = Path(analyst_agent.__file__).with_name("skills")
        backend, filesystem = mount_agent_skills(
            StateBackend(),
            skill_directory,
            [_ANALYST_SKILLS_PATH],
        )
        skill_path = f"{_ANALYST_SKILLS_PATH}analysis/SKILL.md"
        original = backend.read(skill_path)
        write_tool = next(
            tool for tool in filesystem.tools if tool.name == "write_file"
        )
        runtime = ToolRuntime(
            state={},
            context=None,
            config={},
            stream_writer=lambda _: None,
            tool_call_id="write-skill",
            store=None,
        )

        response = cast(Any, write_tool).func(
            file_path=skill_path,
            content="overwritten",
            runtime=runtime,
        )

        self.assertIsInstance(response, ToolMessage)
        self.assertEqual(response.status, "error")
        self.assertIn("permission denied", str(response.content))
        self.assertEqual(backend.read(skill_path).file_data, original.file_data)

    def test_packaged_skills_are_mounted_at_agent_specific_path(self) -> None:
        mounts = packaged_agent_skill_mounts()

        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].target.as_posix(), "/skills/analyst")
        self.assertEqual(
            mounts[0].source,
            Path(analyst_agent.__file__).with_name("skills").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
