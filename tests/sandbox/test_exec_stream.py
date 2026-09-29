import errno
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.sandbox.backend import DockerSandboxBackend
from app.sandbox.docker_stream import close_exec_stream
from tests.sandbox.fakes import FakeSandboxOwnership


def test_disconnected_stream_preserves_command_result():
    stream = MagicMock()
    stream.__iter__.return_value = iter([b"file contents\n"])
    stream.close.side_effect = OSError(errno.ENOTCONN, "Socket is not connected")
    container = MagicMock()
    api = container.client.api
    api.exec_create.return_value = {"Id": "test-exec"}
    api.exec_start.return_value = stream
    api.exec_inspect.return_value = {"ExitCode": 0}
    backend = DockerSandboxBackend(
        1,
        uuid4(),
        10001,
        FakeSandboxOwnership(),
        lambda: None,
        MagicMock(return_value=container),
    )

    result = backend.execute("cat example.txt")

    assert result.output == "file contents\n"
    assert result.exit_code == 0
    stream._response.close.assert_called_once_with()
    api.exec_inspect.assert_called_once_with("test-exec")


@pytest.mark.parametrize(
    "error", [None, OSError(errno.EIO, "I/O error"), RuntimeError("failed")]
)
def test_stream_cleanup_closes_response_and_preserves_other_errors(error):
    stream = MagicMock()
    stream.close.side_effect = error
    if error is None:
        close_exec_stream(stream)
    else:
        with pytest.raises(type(error)) as caught:
            close_exec_stream(stream)
        assert caught.value is error
    stream._response.close.assert_called_once_with()


@pytest.mark.parametrize("exit_code", [0, 2])
def test_shell_disconnected_stream_preserves_output_and_exit_code(exit_code):
    stream = MagicMock()
    stream.__iter__.return_value = iter([])
    stream.close.side_effect = OSError(errno.ENOTCONN, "Socket is not connected")
    container = MagicMock()
    api = container.client.api
    api.exec_create.return_value = {"Id": "shell-exec"}
    api.exec_start.return_value = stream
    api.exec_inspect.return_value = {"ExitCode": 0}
    container.exec_run.return_value.exit_code = 0
    container.exec_run.return_value.output = json.dumps(
        {"status": "finished", "exit_code": exit_code}
    ).encode()
    backend = DockerSandboxBackend(
        1,
        uuid4(),
        10001,
        FakeSandboxOwnership(),
        lambda: None,
        MagicMock(return_value=container),
    )
    with patch.object(
        backend,
        "_read_limited_file_bytes_unlocked",
        return_value=(b"command output\n", 0),
    ):
        result = backend._shell_jobs.run("job_1234abcd", "ls")

    assert result.status == ("completed" if exit_code == 0 else "failed")
    assert result.exit_code == exit_code
    assert result.output == "command output\n"
    assert result.error is None
    stream._response.close.assert_called_once_with()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_shell_cancel_uses_command_exit_status(exit_code):
    container = MagicMock()
    container.exec_run.return_value.exit_code = exit_code
    container.exec_run.return_value.output = b"" if exit_code == 0 else b"cancel failed"
    backend = DockerSandboxBackend(
        1,
        uuid4(),
        10001,
        FakeSandboxOwnership(),
        lambda: None,
        MagicMock(return_value=container),
    )
    if exit_code == 0:
        assert backend._shell_jobs.cancel("job_1234abcd") is None
    else:
        with pytest.raises(OSError, match="cancel failed"):
            backend._shell_jobs.cancel("job_1234abcd")
