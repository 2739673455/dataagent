"""统一路径解析与各文件操作的访问范围。"""

from uuid import UUID, uuid4

import pytest

from app.sandbox import resolve_attachment_path, resolve_sandbox_path
from app.sandbox.contracts import SandboxArtifact, SandboxSessionScope
from app.sandbox.errors import SandboxPathError
from app.sandbox.paths import resolve_artifact_path

_CONVERSATION_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
_ROOT = f"/data/{_CONVERSATION_ID}"


@pytest.mark.parametrize("field", ["analysis_id", "agent_type", "session_id"])
@pytest.mark.parametrize("value", ["", "../outside", "a/b", "a" * 65])
def test_session_scope_rejects_unsafe_workspace_components(field, value):
    fields = {"analysis_id": "daily", "agent_type": "scheduler", "session_id": "report"}
    fields[field] = value
    with pytest.raises(ValueError):
        SandboxSessionScope(**fields)


@pytest.mark.parametrize(
    "path",
    [
        "uploads/report.csv",
        "./uploads/report.csv",
        "uploads/tmp/../report.csv",
        f"{_ROOT}/uploads/report.csv",
    ],
)
def test_attachment_and_agent_resolve_the_same_file(path: str) -> None:
    attachment = resolve_attachment_path(path, _CONVERSATION_ID)
    assert attachment == SandboxArtifact(
        f"{_ROOT}/uploads/report.csv", "uploads/report.csv"
    )
    assert resolve_sandbox_path(path, _ROOT) == attachment.path


@pytest.mark.parametrize(
    "path",
    [
        "report.csv",
        "uploads/report.csv",
        "uploads/tmp/../report.csv",
        f"{_ROOT}/uploads/report.csv",
    ],
)
def test_attachment_mutations_resolve_into_uploads(path: str) -> None:
    assert resolve_attachment_path(path, _CONVERSATION_ID, writable=True) == (
        SandboxArtifact(f"{_ROOT}/uploads/report.csv", "uploads/report.csv")
    )


@pytest.mark.parametrize(
    "path",
    [
        "../report.csv",
        "uploads/../sessions/report.csv",
        f"{_ROOT}/sessions/report.csv",
        f"{_ROOT}/uploads-extra/report.csv",
        "uploads",
    ],
)
def test_attachment_mutations_cannot_escape_uploads(path: str) -> None:
    with pytest.raises(SandboxPathError):
        resolve_attachment_path(path, _CONVERSATION_ID, writable=True)


@pytest.mark.parametrize(
    "path",
    [
        "../other/report.csv",
        f"/data/{uuid4()}/uploads/report.csv",
        f"{_ROOT}-other/uploads/report.csv",
        "/skills/analyst/report.csv",
        ".",
    ],
)
def test_attachment_reads_cannot_escape_the_conversation(path: str) -> None:
    with pytest.raises(SandboxPathError):
        resolve_attachment_path(path, _CONVERSATION_ID)


def test_session_output_aliases_resolve_within_the_same_scope() -> None:
    scope = SandboxSessionScope("analysis", "analyst", "session")
    root = scope.workspace_path(_CONVERSATION_ID)
    for path in ("report.csv", "tmp/../report.csv", f"{root}/report.csv"):
        artifact = resolve_artifact_path(path, _CONVERSATION_ID, scope)
        assert artifact.path == f"{root}/report.csv"
    with pytest.raises(SandboxPathError):
        resolve_artifact_path("../other/report.csv", _CONVERSATION_ID, scope)


@pytest.mark.parametrize("path", ["", "~/report.csv", "a\\b", "a\nb", "a\x7fb"])
def test_all_file_entries_reject_invalid_path_text(path: str) -> None:
    for resolve in (
        lambda: resolve_sandbox_path(path, _ROOT),
        lambda: resolve_attachment_path(path, _CONVERSATION_ID),
        lambda: resolve_artifact_path(path, _CONVERSATION_ID),
    ):
        with pytest.raises(SandboxPathError):
            resolve()


@pytest.mark.parametrize("path", ["数" * 86, "/".join(["a" * 254] * 16)])
def test_resolved_paths_enforce_byte_limits(path: str) -> None:
    with pytest.raises(SandboxPathError):
        resolve_sandbox_path(path, _ROOT)
