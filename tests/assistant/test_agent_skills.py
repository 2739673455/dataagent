import unittest
from pathlib import PurePosixPath
from typing import Any, cast

from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage

from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_specialist_filesystem,
    packaged_skill_readonly_mounts,
)
from app.assistant.resources import ASSISTANT_RESOURCES_DIR
from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.paths import SandboxReadonlyMount

_ANALYST_SKILLS_PATH = agent_skills_mount_path("analyst")


class AgentSkillsTest(unittest.TestCase):
    def test_skills_mount_from_external_resource_directory(self) -> None:
        mounts = packaged_skill_readonly_mounts()
        analyst = next(
            mount for mount in mounts if str(mount.target) == "/skills/analyst"
        )
        self.assertEqual(analyst.source, ASSISTANT_RESOURCES_DIR / "analyst" / "skills")
        self.assertTrue((analyst.source / "analysis" / "SKILL.md").is_file())
        self.assertTrue((analyst.source / "visualization" / "SKILL.md").is_file())

    def test_agent_cannot_modify_mounted_skill(self) -> None:
        state_backend = StateBackend()
        cast(
            Any, state_backend
        ).workspace_dir = "/data/conversation/sessions/analysis/analyst/session"
        filesystem = build_specialist_filesystem(
            cast(DockerSandboxBackend, state_backend),
            skill_mount=SandboxReadonlyMount(
                source=ASSISTANT_RESOURCES_DIR / "analyst" / "skills",
                target=PurePosixPath(_ANALYST_SKILLS_PATH),
            ),
        )
        backend = filesystem.backend
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
