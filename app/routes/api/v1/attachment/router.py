import mimetypes
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from loguru import logger

from app.clients.docker_sandbox_manager import (
    SandboxFileTooLargeError,
    SandboxPathError,
    SandboxStorageLimitError,
    docker_sandbox_manager,
)
from app.errors import attachment_error, chat_error
from app.routes.api.v1.chat import schemas as chat_schema
from app.routes.api.v1.chat.dependencies import ConversationPGRepoDep

router = APIRouter(tags=["attachment"])


@router.post("/upload")
async def api_upload_attachment(
    request: Request,
    conversation_repo: ConversationPGRepoDep,
    conversation_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> chat_schema.UploadAttachmentResponse:
    """上传附件到当前会话工作区"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get(user_id, conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    # 获取文件名
    f_path = file.filename or "upload"
    try:
        await docker_sandbox_manager.upload_file(
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

    logger.info(f"Upload attachment: {conversation_id=}, file={f_path}")
    return chat_schema.UploadAttachmentResponse(
        attachment=chat_schema.Attachment(f_path=f_path)
    )


@router.post("/delete")
async def api_delete_attachment(
    request: Request,
    body: chat_schema.DeleteAttachmentRequest,
    conversation_repo: ConversationPGRepoDep,
) -> None:
    """删除当前会话工作区中的附件"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get(user_id, body.conversation_id)
    if conversation is None:
        raise chat_error.ConversationNotFoundError

    try:
        await docker_sandbox_manager.delete_file(
            user_id,
            body.conversation_id,
            body.f_path,
        )
    except SandboxPathError:
        raise attachment_error.PathTraversalError from None

    logger.info(
        f"Delete attachment: conversation_id={body.conversation_id}, file={body.f_path}"
    )


@router.get("/get")
async def api_get_attachment(
    request: Request,
    conversation_id: UUID,
    f_path: str,
    conversation_repo: ConversationPGRepoDep,
) -> Response:
    """获取当前会话工作区中的附件文件"""
    user_id = request.state.payload.sub
    # 检查对话是否存在且属于当前用户
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

    logger.info(f"Get attachment: {conversation_id=}, file={f_path}")
    return Response(
        content=content,
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(f_path.rsplit('/', 1)[-1])}"
            )
        },
    )
