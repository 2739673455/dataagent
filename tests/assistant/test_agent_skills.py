import unittest
from pathlib import Path
from typing import Any, cast

from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage

from app.assistant.agents.analyst import agent as analyst_agent
from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_specialist_filesystem,
)
from app.sandbox.backend import DockerSandboxBackend

_ANALYST_SKILLS_PATH = agent_skills_mount_path("analyst")


class AgentSkillsTest(unittest.TestCase):
    def test_agent_cannot_modify_mounted_skill(self) -> None:
        skill_directory = Path(analyst_agent.__file__).with_name("skills")
        state_backend = StateBackend()
        cast(
            Any, state_backend
        ).workspace_dir = "/data/conversation/sessions/analysis/analyst/session"
        backend, filesystem = build_specialist_filesystem(
            cast(DockerSandboxBackend, state_backend),
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


if __name__ == "__main__":
    unittest.main()
