"""附件业务服务依赖。"""

from typing import Annotated

from fastapi import Depends

from app.assistant.api.chat.dependencies import ConversationPGRepoDep
from app.assistant.api.dependencies import (
    SandboxManagerDep,
)
from app.assistant.services.attachments import AttachmentService


def _get_attachment_service(
    repository: ConversationPGRepoDep,
    sandbox: SandboxManagerDep,
) -> AttachmentService:
    """绑定当前请求的附件服务资源。"""
    return AttachmentService(repository, sandbox)


AttachmentServiceDep = Annotated[AttachmentService, Depends(_get_attachment_service)]
