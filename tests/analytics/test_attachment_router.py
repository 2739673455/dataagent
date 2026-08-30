import io
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import UploadFile

from app.analytics.api.attachment.router import (
    api_delete_attachment,
    api_get_attachment,
    api_upload_attachment,
)
from app.analytics.api.chat.schemas import DeleteAttachmentRequest
from app.sandbox import errors as attachment_error
from app.sandbox.exceptions import SandboxPathError, SandboxStorageLimitError


class AsyncContextStub:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class AttachmentRouterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user = MagicMock(id=7)
        self.conversation_id = uuid4()
        self.conversation = object()
        self.conversation_repo = MagicMock()
        self.conversation_repo.get = AsyncMock(return_value=self.conversation)
        self.conversation_repo.update = AsyncMock()
        self.sandbox = MagicMock()
        self.sandbox.upload_user_attachment = AsyncMock()
        self.sandbox.delete_user_attachment = AsyncMock()
        self.sandbox.download_file = AsyncMock()
        self.lifecycle = MagicMock()
        self.lifecycle.lock.return_value = AsyncContextStub()

    async def test_upload_uses_user_attachment_capability_and_normalized_path(
        self,
    ) -> None:
        upload = AsyncMock(return_value="uploads/report.csv")
        file = UploadFile(
            file=io.BytesIO(b"report"),
            filename="uploads//report.csv",
        )

        self.sandbox.upload_user_attachment = upload
        response = await api_upload_attachment(
            conversation_repo=self.conversation_repo,
            current_user=self.user,
            lifecycle=self.lifecycle,
            sandbox=self.sandbox,
            conversation_id=self.conversation_id,
            file=file,
        )

        upload.assert_awaited_once_with(
            7,
            self.conversation_id,
            "uploads//report.csv",
            file.file,
        )
        self.assertEqual(response.attachment.f_path, "uploads/report.csv")
        self.conversation_repo.update.assert_awaited_once_with(self.conversation)

    async def test_upload_and_delete_map_unsafe_path_error(self) -> None:
        file = UploadFile(
            file=io.BytesIO(b"overwrite"),
            filename="../report.csv",
        )

        self.sandbox.upload_user_attachment.side_effect = SandboxPathError(
            "../report.csv"
        )
        with self.assertRaises(attachment_error.PathTraversalError):
            await api_upload_attachment(
                conversation_repo=self.conversation_repo,
                current_user=self.user,
                lifecycle=self.lifecycle,
                sandbox=self.sandbox,
                conversation_id=self.conversation_id,
                file=file,
            )
        self.sandbox.delete_user_attachment.side_effect = SandboxPathError(
            "../report.csv"
        )
        with self.assertRaises(attachment_error.PathTraversalError):
            await api_delete_attachment(
                body=DeleteAttachmentRequest(
                    conversation_id=self.conversation_id,
                    f_path="../report.csv",
                ),
                conversation_repo=self.conversation_repo,
                current_user=self.user,
                lifecycle=self.lifecycle,
                sandbox=self.sandbox,
            )

    async def test_upload_maps_workspace_limit_error(self) -> None:
        file = UploadFile(file=io.BytesIO(b"report"), filename="report.csv")
        self.sandbox.upload_user_attachment.side_effect = SandboxStorageLimitError(
            "工作区容量超出限制"
        )

        with self.assertRaises(attachment_error.SandboxStorageLimitProblem):
            await api_upload_attachment(
                conversation_repo=self.conversation_repo,
                current_user=self.user,
                lifecycle=self.lifecycle,
                sandbox=self.sandbox,
                conversation_id=self.conversation_id,
                file=file,
            )

    async def test_delete_uses_user_attachment_capability(self) -> None:
        delete = AsyncMock()
        self.sandbox.delete_user_attachment = delete
        await api_delete_attachment(
            body=DeleteAttachmentRequest(
                conversation_id=self.conversation_id,
                f_path="uploads/report.csv",
            ),
            conversation_repo=self.conversation_repo,
            current_user=self.user,
            lifecycle=self.lifecycle,
            sandbox=self.sandbox,
        )

        delete.assert_awaited_once_with(
            7,
            self.conversation_id,
            "uploads/report.csv",
        )
        self.conversation_repo.update.assert_awaited_once_with(self.conversation)

    async def test_session_artifact_remains_downloadable(self) -> None:
        download = AsyncMock(return_value=b"verified artifact")
        self.sandbox.download_file = download
        response = await api_get_attachment(
            conversation_id=self.conversation_id,
            f_path="sessions/run/analyst/main/report.csv",
            conversation_repo=self.conversation_repo,
            current_user=self.user,
            lifecycle=self.lifecycle,
            sandbox=self.sandbox,
        )

        download.assert_awaited_once_with(
            7,
            self.conversation_id,
            "sessions/run/analyst/main/report.csv",
        )
        self.assertEqual(response.body, b"verified artifact")


if __name__ == "__main__":
    unittest.main()
