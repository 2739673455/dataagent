import io
import tarfile
import time
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.sandbox.archive import SandboxArchiveStore
from app.sandbox.backend import DockerSandboxBackend
from tests.sandbox.fakes import FakeSandboxOwnership


@pytest.mark.parametrize("upload", [False, True])
def test_written_archive_has_current_modification_times(upload):
    archives = []
    container = MagicMock()
    container.exec_run.return_value.exit_code = 0

    def capture_archive(path, buffer):
        archives.append(buffer.read())
        return True

    container.put_archive.side_effect = capture_archive
    before = int(time.time())
    if upload:
        backend = DockerSandboxBackend(
            1,
            uuid4(),
            10001,
            FakeSandboxOwnership(),
            lambda: None,
            MagicMock(return_value=container),
        )
        with backend._operation():
            backend._put_archive(
                f"{backend.workspace_dir}/example.txt", io.BytesIO(b"test"), 4
            )
    else:
        SandboxArchiveStore(max_file_bytes=1024)._put(
            container,
            "/data",
            [("example", 10001, 10001, 0o700)],
            [("example/file.txt", 10001, 10001, 0o600, io.BytesIO(b"test"), 4)],
        )
    after = int(time.time())

    assert len(archives) == 1
    with tarfile.open(fileobj=io.BytesIO(archives[0])) as archive:
        members = archive.getmembers()
        assert len(members) == (1 if upload else 2)
        assert all(before <= member.mtime <= after for member in members)
        file = next(member for member in members if member.isfile())
        content = archive.extractfile(file)
        assert content is not None
        assert content.read() == b"test"
