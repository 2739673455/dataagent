"""会话附件业务操作与沙箱异常转换。"""

from typing import BinaryIO
from uuid import UUID

from app.assistant import errors
from app.assistant.application.lifecycle import ConversationLifecycleService
from app.assistant.repositories.conversation import ConversationPGRepo
from app.sandbox.application import DockerSandboxManager
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from app.shared.errors.infrastructure import AdvisoryLockBusyError


class AttachmentService:
    """校验会话归属并管理附件。"""

    def __init__(
        self,
        repository: ConversationPGRepo,
        lifecycle: ConversationLifecycleService,
        sandbox: DockerSandboxManager,
    ) -> None:
        """绑定会话仓储、生命周期锁和沙箱文件操作能力。"""
        self._repository = repository
        self._lifecycle = lifecycle
        self._sandbox = sandbox

    async def upload(
        self, user_id: int, conversation_id: UUID, f_path: str, content: BinaryIO
    ) -> str:
        """在会话锁内校验归属并上传附件，返回规范化的会话相对路径。"""
        try:
            async with self._lifecycle.lock(user_id, conversation_id):
                conversation = await self._repository.get(user_id, conversation_id)
                if conversation is None:
                    raise errors.ConversationNotFoundError

                try:
                    f_path = await self._sandbox.upload_user_attachment(
                        user_id,
                        conversation_id,
                        f_path,
                        content,
                    )
                except SandboxPathError:
                    raise errors.PathTraversalError from None
                except SandboxFileTooLargeError:
                    raise errors.AttachmentTooLargeError from None
                await self._repository.update(conversation)
        except AdvisoryLockBusyError as exc:
            raise errors.ConversationBusyError(detail=str(exc)) from exc
        return f_path

    async def delete(self, user_id: int, conversation_id: UUID, f_path: str) -> None:
        """在会话锁内校验归属并删除用户附件，更新会话时间。"""
        try:
            async with self._lifecycle.lock(user_id, conversation_id):
                conversation = await self._repository.get(user_id, conversation_id)
                if conversation is None:
                    raise errors.ConversationNotFoundError

                try:
                    await self._sandbox.delete_user_attachment(
                        user_id,
                        conversation_id,
                        f_path,
                    )
                except SandboxPathError:
                    raise errors.PathTraversalError from None
                await self._repository.update(conversation)
        except AdvisoryLockBusyError as exc:
            raise errors.ConversationBusyError(detail=str(exc)) from exc

    async def download(self, user_id: int, conversation_id: UUID, f_path: str) -> bytes:
        """校验会话归属并读取文件内容，将沙箱异常转换为附件业务异常。"""
        conversation = await self._repository.get(user_id, conversation_id)
        if conversation is None:
            raise errors.ConversationNotFoundError

        try:
            content = await self._sandbox.download_file(
                user_id,
                conversation_id,
                f_path,
            )
        except SandboxPathError:
            raise errors.PathTraversalError from None
        except FileNotFoundError:
            raise errors.AttachmentNotFoundError(detail=f_path) from None
        except SandboxFileTooLargeError:
            raise errors.AttachmentTooLargeError from None
        return content
