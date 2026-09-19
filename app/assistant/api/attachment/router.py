"""会话附件上传、下载与删除路由。"""

import mimetypes
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response
from loguru import logger

from app.assistant.api.attachment.dependencies import AttachmentServiceDep
from app.assistant.events import schemas as chat_schema
from app.identity.api.auth.dependencies import AnalysisUserDep, CurrentUserDep

router = APIRouter(tags=["attachment"])


@router.post("/upload")
async def api_upload_attachment(
    service: AttachmentServiceDep,
    current_user: AnalysisUserDep,
    conversation_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> chat_schema.UploadAttachmentResponse:
    """上传附件到当前会话工作区。"""
    user_id = current_user.id
    f_path = await service.upload(
        user_id, conversation_id, file.filename or "upload", file.file
    )

    logger.info(f"上传附件: conversation_id={conversation_id}, file={f_path}")
    return chat_schema.UploadAttachmentResponse(
        attachment=chat_schema.Attachment(f_path=f_path)
    )


@router.post("/delete")
async def api_delete_attachment(
    body: chat_schema.DeleteAttachmentRequest,
    service: AttachmentServiceDep,
    current_user: CurrentUserDep,
) -> None:
    """删除当前会话工作区中的附件。"""
    user_id = current_user.id
    await service.delete(user_id, body.conversation_id, body.f_path)

    logger.info(f"删除附件: conversation_id={body.conversation_id}, file={body.f_path}")


@router.get("/get")
async def api_get_attachment(
    conversation_id: UUID,
    f_path: str,
    service: AttachmentServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """获取当前会话工作区中的附件文件。"""
    user_id = current_user.id
    content = await service.download(user_id, conversation_id, f_path)

    # 获取文件 MIME 类型。
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
