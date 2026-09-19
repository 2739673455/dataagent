"""会话附件业务操作与沙箱异常转换。"""

from typing import BinaryIO
from uuid import UUID

from app.assistant import errors
from app.assistant.conversations.lifecycle import ConversationLifecycleService
from app.assistant.repositories.conversation import ConversationPGRepo
from app.sandbox.exceptions import SandboxFileTooLargeError, SandboxPathError
from app.sandbox.manager import DockerSandboxManager
from app.shared.clients.langgraph_postgres_manager import AdvisoryLockBusyError


class AttachmentService:
    """校验会话归属并管理附件。"""

    def __init__(
        self,
        repository: ConversationPGRepo,
        lifecycle: ConversationLifecycleService,
        sandbox: DockerSandboxManager,
    ) -> None:
        self._repository = repository
        self._lifecycle = lifecycle
        self._sandbox = sandbox

    async def upload(
        self, user_id: int, conversation_id: UUID, f_path: str, content: BinaryIO
    ) -> str:
        try:
            async with self._lifecycle.lock(user_id, conversation_id):
                # 检查对话是否存在且属于当前用户。
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
