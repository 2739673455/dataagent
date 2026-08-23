import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import UploadFile

from app.analytics.api.attachment.router import (
    api_delete_attachment,
    api_get_attachment,
    api_upload_attachment,
    conversation_lifecycle_service,
    docker_sandbox_manager,
)
from app.analytics.api.chat.schemas import DeleteAttachmentRequest
from app.sandbox import errors as attachment_error


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
        lock_patcher = patch.object(
            conversation_lifecycle_service,
            "lock",
            return_value=AsyncContextStub(),
        )
        lock_patcher.start()
        self.addCleanup(lock_patcher.stop)

    async def test_upload_uses_user_attachment_capability_and_normalized_path(
        self,
    ) -> None:
        upload = AsyncMock(return_value="uploads/report.csv")
        file = UploadFile(
            file=io.BytesIO(b"report"),
            filename="uploads//report.csv",
        )

        with patch.object(
            docker_sandbox_manager,
            "upload_user_attachment",
            upload,
        ):
            response = await api_upload_attachment(
                conversation_repo=self.conversation_repo,
                current_user=self.user,
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

    async def test_upload_and_delete_reject_analysis_artifact_path(self) -> None:
        file = UploadFile(
            file=io.BytesIO(b"overwrite"),
            filename="analyses/run/report.csv",
        )

        with self.assertRaises(attachment_error.PathTraversalError):
            await api_upload_attachment(
                conversation_repo=self.conversation_repo,
                current_user=self.user,
                conversation_id=self.conversation_id,
                file=file,
            )
        with self.assertRaises(attachment_error.PathTraversalError):
            await api_delete_attachment(
                body=DeleteAttachmentRequest(
                    conversation_id=self.conversation_id,
                    f_path="analyses/run/report.csv",
                ),
                conversation_repo=self.conversation_repo,
                current_user=self.user,
            )

    async def test_delete_uses_user_attachment_capability(self) -> None:
        delete = AsyncMock()
        with patch.object(
            docker_sandbox_manager,
            "delete_user_attachment",
            delete,
        ):
            await api_delete_attachment(
                body=DeleteAttachmentRequest(
                    conversation_id=self.conversation_id,
                    f_path="uploads/report.csv",
                ),
                conversation_repo=self.conversation_repo,
                current_user=self.user,
            )

        delete.assert_awaited_once_with(
            7,
            self.conversation_id,
            "uploads/report.csv",
        )
        self.conversation_repo.update.assert_awaited_once_with(self.conversation)

    async def test_analysis_artifact_remains_downloadable(self) -> None:
        download = AsyncMock(return_value=b"verified artifact")
        with patch.object(
            docker_sandbox_manager,
            "download_file",
            download,
        ):
            response = await api_get_attachment(
                conversation_id=self.conversation_id,
                f_path="analyses/run/report.csv",
                conversation_repo=self.conversation_repo,
                current_user=self.user,
            )

        download.assert_awaited_once_with(
            7,
            self.conversation_id,
            "analyses/run/report.csv",
        )
        self.assertEqual(response.body, b"verified artifact")


if __name__ == "__main__":
    unittest.main()
