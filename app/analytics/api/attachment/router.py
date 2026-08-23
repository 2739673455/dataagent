"""会话附件上传、下载与删除路由"""

import mimetypes
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response
from loguru import logger

from app.analytics import errors as chat_error
from app.analytics.api.chat import schemas as chat_schema
from app.analytics.api.chat.dependencies import ConversationPGRepoDep
from app.analytics.services.conversation_lifecycle import (
    conversation_lifecycle_service,
)
from app.identity.api.auth.dependencies import AnalysisUserDep, CurrentUserDep
from app.sandbox import errors as attachment_error
from app.sandbox.docker_manager import (
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxStorageLimitError,
    docker_sandbox_manager,
)

router = APIRouter(tags=["attachment"])


@router.post("/upload")
async def api_upload_attachment(
    conversation_repo: ConversationPGRepoDep,
    current_user: AnalysisUserDep,
    conversation_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> chat_schema.UploadAttachmentResponse:
    """上传附件到当前会话工作区"""
    user_id = current_user.id
    async with conversation_lifecycle_service.lock(user_id, conversation_id):
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get(user_id, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError

        # 获取文件名
        f_path = file.filename or "upload"
        try:
            f_path = await docker_sandbox_manager.upload_user_attachment(
                user_id,
                conversation_id,
                f_path,
                file.file,
            )
        except SandboxPathError:
            raise attachment_error.PathTraversalError from None
        except SandboxFileTooLargeError:
            raise attachment_error.AttachmentTooLargeError from None
        except SandboxStorageLimitError:
            raise attachment_error.SandboxStorageLimitError from None
        await conversation_repo.update(conversation)

    logger.info(f"上传附件: conversation_id={conversation_id}, file={f_path}")
    return chat_schema.UploadAttachmentResponse(
        attachment=chat_schema.Attachment(f_path=f_path)
    )


@router.post("/delete")
async def api_delete_attachment(
    body: chat_schema.DeleteAttachmentRequest,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
) -> None:
    """删除当前会话工作区中的附件"""
    user_id = current_user.id
    async with conversation_lifecycle_service.lock(user_id, body.conversation_id):
        conversation = await conversation_repo.get(user_id, body.conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError

        try:
            await docker_sandbox_manager.delete_user_attachment(
                user_id,
                body.conversation_id,
                body.f_path,
            )
        except SandboxPathError:
            raise attachment_error.PathTraversalError from None
        await conversation_repo.update(conversation)

    logger.info(
        f"删除附件: conversation_id={body.conversation_id}, file={body.f_path}"
    )


@router.get("/get")
async def api_get_attachment(
    conversation_id: UUID,
    f_path: str,
    conversation_repo: ConversationPGRepoDep,
    current_user: CurrentUserDep,
) -> Response:
    """获取当前会话工作区中的附件文件"""
    user_id = current_user.id
    async with conversation_lifecycle_service.lock(user_id, conversation_id):
        conversation = await conversation_repo.get(user_id, conversation_id)
        if conversation is None:
            raise chat_error.ConversationNotFoundError

        try:
            content = await docker_sandbox_manager.download_file(
                user_id,
                conversation_id,
                f_path,
            )
        except SandboxPathError:
            raise attachment_error.PathTraversalError from None
        except FileNotFoundError:
            raise attachment_error.AttachmentNotFoundError(detail=f_path)
        except SandboxFileTooLargeError:
            raise attachment_error.AttachmentTooLargeError from None

    # 获取文件 MIME 类型
    media_type, _ = mimetypes.guess_type(f_path)

    logger.info(f"获取附件: conversation_id={conversation_id}, file={f_path}")
    return Response(
        content=content,
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(f_path.rsplit('/', 1)[-1])}"
            )
        },
    )
