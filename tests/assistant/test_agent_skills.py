import tempfile
import unittest
from contextlib import chdir
from typing import Any, cast

from deepagents.backends import StateBackend
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage

from app.assistant.agents.filesystem import (
    agent_skills_mount_path,
    build_agent_filesystem,
    packaged_skill_readonly_mounts,
)
from app.assistant.resource_loader import SKILLS_DIRECTORY, load_prompt
from app.sandbox.backend import DockerSandboxBackend

_ANALYST_SKILLS_PATH = agent_skills_mount_path("analyst")


class AgentSkillsTest(unittest.TestCase):
    def test_packaged_skills_load_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            mounts = packaged_skill_readonly_mounts()
            analyst = next(
                mount for mount in mounts if str(mount.target) == "/skills/analyst"
            )
            self.assertTrue(analyst.source.is_absolute())
            self.assertIn(
                "name: analysis", (analyst.source / "analysis/SKILL.md").read_text()
            )
            self.assertIn(
                "name: visualization",
                (analyst.source / "visualization/SKILL.md").read_text(),
            )

    def test_prompts_load_outside_repository_and_render_runtime_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            for role in ("planner", "explorer", "analyst", "reviewer"):
                with self.subTest(role=role):
                    self.assertIn("# 角色定位", load_prompt(f"agents/{role}"))
            workspace = "/data/session-{literal}"
            for writable in (False, True):
                state_backend = StateBackend()
                cast(Any, state_backend).workspace_dir = workspace
                _, filesystem = build_agent_filesystem(
                    cast(DockerSandboxBackend, state_backend),
                    tools=["read_file", "write_file", "edit_file"]
                    if writable
                    else ["read_file"],
                )
                prompt = filesystem._custom_system_prompt
                assert prompt is not None
                self.assertIn(workspace, prompt)
                self.assertIn("[[DATAAGENT_ARTIFACT:<absolute_path>]]", prompt)
                self.assertIn("只能修改当前 Session", prompt)
            self.assertIn(
                "不得超过 64 个字符",
                load_prompt("conversation_title").format(max_title_length=64),
            )

    def test_agent_cannot_modify_mounted_skill(self) -> None:
        skill_directory = SKILLS_DIRECTORY
        state_backend = StateBackend()
        cast(
            Any, state_backend
        ).workspace_dir = "/data/conversation/sessions/analysis/analyst/session"
        backend, filesystem = build_agent_filesystem(
            cast(DockerSandboxBackend, state_backend),
            tools=["read_file", "write_file", "edit_file"],
            skill_directory=skill_directory,
            skills=[_ANALYST_SKILLS_PATH],
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
