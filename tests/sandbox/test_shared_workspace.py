"""共享会话目录保留跨会话边界与文件交付能力。"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.errors import SandboxPathError
from app.sandbox.paths import conversation_relative_path
from tests.sandbox.fakes import FakeSandboxOwnership


def test_shared_backend_resolves_tools_and_artifact_paths_to_conversation():
    conversation = uuid4()
    backend = DockerSandboxBackend(
        7,
        conversation,
        10001,
        FakeSandboxOwnership(),
        lambda: None,
        MagicMock(),
    )
    root = f"/data/{conversation}"
    assert backend.workspace_dir == root
    for path in ("query.csv", "uploads/image.png", "report/index.html"):
        assert backend._resolve_path(path) == f"{root}/{path}"
        assert backend._resolve_mutation_path(path) == f"{root}/{path}"
        assert conversation_relative_path(f"{root}/{path}", conversation) == path
    with pytest.raises(SandboxPathError):
        backend._resolve_mutation_path(f"/data/{uuid4()}/report.html")
    for path in (
        f"/data/{uuid4()}/query.csv",
        f"{root}/../query.csv",
        f"{root}/.home/key",
        root,
    ):
        with pytest.raises(SandboxPathError):
            conversation_relative_path(path, conversation)
