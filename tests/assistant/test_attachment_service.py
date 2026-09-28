"""产物下载的底层错误转换。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.assistant import errors
from app.assistant.conversations.attachments import AttachmentService
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError


@pytest.mark.parametrize(
    "failure, expected",
    [
        (SandboxPathError(), errors.PathTraversalError),
        (SandboxFileTooLargeError(), errors.AttachmentTooLargeError),
        (FileNotFoundError(), errors.AttachmentNotFoundError),
    ],
)
def test_download_preserves_business_error(failure, expected):
    repository = MagicMock(get=AsyncMock(return_value=MagicMock()))
    sandbox = MagicMock(
        download_file=AsyncMock(side_effect=failure),
    )
    service = AttachmentService(repository, sandbox)
    args = (1, uuid4(), "sessions/analysis/analyst/report/a.csv")
    with pytest.raises(expected):
        asyncio.run(service.download(*args))
