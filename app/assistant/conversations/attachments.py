"""会话附件业务操作与沙箱异常转换。"""

from uuid import UUID

from app.assistant import errors
from app.assistant.repositories.conversation import ConversationPGRepo
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError
from app.sandbox.manager import DockerSandboxManager


class AttachmentService:
    """校验会话归属并管理附件。"""

    def __init__(
        self,
        repository: ConversationPGRepo,
        sandbox: DockerSandboxManager,
    ) -> None:
        self._repository = repository
        self._sandbox = sandbox

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
