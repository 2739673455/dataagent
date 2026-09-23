"""附件业务边界：底层错误转换、权限检查和目录更新。"""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.assistant import errors
from app.assistant.conversations.attachments import AttachmentService
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from app.shared.errors.infrastructure import AdvisoryLockBusyError


@pytest.mark.parametrize(
    "operation, failure, expected",
    [
        ("upload", SandboxPathError(), errors.PathTraversalError),
        ("upload", SandboxFileTooLargeError(), errors.AttachmentTooLargeError),
        ("delete", SandboxPathError(), errors.PathTraversalError),
        ("download", SandboxPathError(), errors.PathTraversalError),
        ("download", SandboxFileTooLargeError(), errors.AttachmentTooLargeError),
        ("download", FileNotFoundError(), errors.AttachmentNotFoundError),
        ("upload", AdvisoryLockBusyError(), errors.ConversationBusyError),
        ("delete", AdvisoryLockBusyError(), errors.ConversationBusyError),
    ],
)
def test_attachment_failures_preserve_business_error_and_do_not_update_directory(
    operation, failure, expected
):
    @asynccontextmanager
    async def lock(*args):
        if isinstance(failure, AdvisoryLockBusyError):
            raise failure
        yield

    repository = MagicMock(get=AsyncMock(return_value=MagicMock()), update=AsyncMock())
    sandbox = MagicMock(
        upload_user_attachment=AsyncMock(side_effect=failure),
        delete_user_attachment=AsyncMock(side_effect=failure),
        download_file=AsyncMock(side_effect=failure),
    )
    service = AttachmentService(repository, MagicMock(lock=lock), sandbox)
    args = (1, uuid4(), "uploads/a.csv")
    if operation == "upload":
        args += (BytesIO(b"data"),)
    with pytest.raises(expected):
        asyncio.run(getattr(service, operation)(*args))
    repository.update.assert_not_awaited()


@pytest.mark.parametrize("missing", [False, True])
def test_upload_checks_conversation_and_updates_only_after_success(missing):
    @asynccontextmanager
    async def lock(*args):
        yield

    conversation = None if missing else MagicMock()
    repository = MagicMock(get=AsyncMock(return_value=conversation), update=AsyncMock())
    sandbox = MagicMock(upload_user_attachment=AsyncMock(return_value="uploads/a.csv"))
    service = AttachmentService(repository, MagicMock(lock=lock), sandbox)
    conversation_id = uuid4()
    content = BytesIO(b"data")
    if missing:
        with pytest.raises(errors.ConversationNotFoundError):
            asyncio.run(service.upload(1, conversation_id, "a.csv", content))
        sandbox.upload_user_attachment.assert_not_awaited()
        repository.update.assert_not_awaited()
    else:
        assert (
            asyncio.run(service.upload(1, conversation_id, "a.csv", content))
            == "uploads/a.csv"
        )
        sandbox.upload_user_attachment.assert_awaited_once_with(
            1, conversation_id, "a.csv", content
        )
        repository.update.assert_awaited_once_with(conversation)
