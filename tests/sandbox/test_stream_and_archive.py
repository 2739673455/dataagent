"""流清理和真实 tar 编解码的边界回归。"""

import errno
import io
import tarfile
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.sandbox.application import DockerSandboxBackend
from app.sandbox.application.shell_runner import DockerShellJobRunner
from app.sandbox.archive import SandboxArchiveStore
from app.sandbox.docker_stream import close_exec_stream
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from tests.sandbox.fakes import FakeSandboxOwnership, build_sandbox_config


def _backend():
    container = MagicMock()
    backend = DockerSandboxBackend(
        7,
        uuid4(),
        100_001,
        build_sandbox_config(),
        FakeSandboxOwnership(),
        lambda: None,
        lambda _: container,
    )
    backend._operation_local.container = container
    return backend, container


def test_agent_reads_and_writes_share_resolution_with_distinct_scopes():
    backend, _ = _backend()
    assert backend._resolve_mutation_path("tmp/../report.csv") == (
        f"{backend.workspace_dir}/report.csv"
    )
    assert backend._resolve_path("/skills/analyst/reference.csv") == (
        "/skills/analyst/reference.csv"
    )
    for path in (
        "../other/report.csv",
        "/skills/analyst/reference.csv",
        f"{backend.workspace_dir}-other/report.csv",
    ):
        with pytest.raises(SandboxPathError):
            backend._resolve_mutation_path(path)


@pytest.mark.parametrize("error_number", [None, errno.ENOTCONN, errno.EIO])
def test_stream_always_releases_response(error_number):
    stream = MagicMock()
    if error_number is not None:
        stream.close.side_effect = OSError(error_number, "close failed")
    if error_number == errno.EIO:
        with pytest.raises(OSError) as error:
            close_exec_stream(stream)
        assert error.value.errno == errno.EIO
    else:
        close_exec_stream(stream)
    stream._response.close.assert_called_once()


@pytest.mark.parametrize("exit_code", [0, 2])
@pytest.mark.parametrize("shell", [False, True])
def test_disconnected_stream_preserves_command_result(exit_code, shell):
    backend, container = _backend()
    stream = MagicMock()
    stream.__iter__.return_value = iter([b"result\n"])
    stream.close.side_effect = OSError(errno.ENOTCONN, "disconnected")
    container.client.api.exec_start.return_value = stream
    container.client.api.exec_inspect.return_value = {"ExitCode": exit_code}
    if shell:
        runner = DockerShellJobRunner(backend)
        with (
            patch.object(
                runner,
                "_read_control",
                return_value={"status": "finished", "exit_code": exit_code},
            ),
            patch.object(
                backend,
                "_read_limited_file_bytes_unlocked",
                return_value=(b"result\n", 0),
            ),
        ):
            result = runner._run_unlocked("job_aaaaaaaa", "command", None)
    else:
        result = backend._execute_unlocked("command")
    assert result.output == "result\n"
    assert result.exit_code == exit_code
    stream._response.close.assert_called_once()


def test_new_archive_entries_have_current_mtime():
    backend, container = _backend()
    captured = []

    def capture(path, buffer):
        with tarfile.open(fileobj=io.BytesIO(buffer.read())) as archive:
            captured.extend(archive.getmembers())
        return True

    container.put_archive.side_effect = capture
    before = int(time.time())
    SandboxArchiveStore(100).put(
        container,
        "/data",
        [("directory", 1, 1, 0o700)],
        [("file", 1, 1, 0o600, io.BytesIO(b"abc"), 3)],
    )
    container.exec_run.return_value = MagicMock(exit_code=0)
    backend._put_archive(f"{backend.workspace_dir}/file", io.BytesIO(b"abc"), 3)
    assert len(captured) == 3
    assert all(before <= entry.mtime <= int(time.time()) for entry in captured)


def test_archive_size_limit_and_truncated_content():
    container = MagicMock()
    store = SandboxArchiveStore(100)
    member = tarfile.TarInfo("file")
    member.size = 4
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(member, io.BytesIO(b"data"))
    raw = buffer.getvalue()
    container.get_archive.return_value = (iter([raw]), {})
    assert store.read_file(container, "/file", 4)[0] == b"data"
    container.get_archive.return_value = (iter([raw]), {})
    with pytest.raises(SandboxFileTooLargeError):
        store.read_file(container, "/file", 3)
    container.get_archive.return_value = (iter([raw[:514]]), {})
    with pytest.raises(tarfile.ReadError):
        store.read_file(container, "/file", 4)


@pytest.mark.parametrize(
    "kind", ["valid", "other_session", "wrong_group", "directory", "oversize"]
)
def test_download_qualification_preserves_session_ownership(kind):
    """相同会话下的另一个 Session UID 不能冒充目标产物的属主。"""
    from app.sandbox.archive import _UidRegistry

    conversation_id = uuid4()
    root = f"/data/{conversation_id}"
    relative = "sessions/analysis/analyst/session/result.csv"
    registry = _UidRegistry(
        {str(conversation_id): 100_001},
        {f"{conversation_id}/sessions/analysis/analyst/session": 100_002},
    )
    store = SandboxArchiveStore(100)

    def inspect(container, path):
        info = tarfile.TarInfo(path)
        info.gid = 100_001
        info.uid = (
            100_002
            if path.startswith(f"{root}/sessions/analysis/analyst/session")
            else 100_001
        )
        info.type = tarfile.REGTYPE if path == f"{root}/{relative}" else tarfile.DIRTYPE
        if path == f"{root}/{relative}":
            info.size = 101 if kind == "oversize" else 4
            if kind == "other_session":
                info.uid = 100_003
            if kind == "wrong_group":
                info.gid = 100_004
            if kind == "directory":
                info.type = tarfile.DIRTYPE
        return info

    with (
        patch.object(store, "_load_registry", return_value=registry),
        patch.object(store, "inspect_path", side_effect=inspect),
    ):
        assert store.is_downloadable_file(MagicMock(), conversation_id, relative) == (
            kind == "valid"
        )
