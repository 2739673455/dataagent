import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent import get_workspace_dir
from app.core.database import get_db
from app.errors import attachment_error, chat_error
from app.repositories import conversation_repo
from app.schemas import chat_schema

router = APIRouter(tags=["attachment"])


def _build_attachment_path(target_dir: Path, path: str) -> Path:
    """基于工作区目录构造附件路径，并阻止路径逃逸"""
    target_path = (target_dir / path).resolve()
    if target_dir.resolve() not in target_path.parents:
        raise attachment_error.PathTraversalError
    return target_path


@router.post("/upload")
async def api_upload_attachment(
    request: Request,
    db_session: Annotated[AsyncSession, Depends(get_db)],
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
) -> chat_schema.UploadAttachmentResponse:
    """上传附件到当前会话工作区"""
    user_id = request.state.payload.sub

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFoundError

    # 获取文件名
    f_path = file.filename or "upload"
    # 构造文件路径
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    target_path = _build_attachment_path(workspace_dir, f_path)
    # 按块读取并落盘，避免一次性把整个文件读入内存
    with target_path.open("wb") as target_file:
        while chunk := await file.read(1024 * 1024):
            target_file.write(chunk)

    logger.info(f"Upload attachment: {conversation_id=}, file={f_path}")
    return chat_schema.UploadAttachmentResponse(attachment=chat_schema.Attachment(f_path=f_path))


@router.post("/delete")
async def api_delete_attachment(
    request: Request,
    body: chat_schema.DeleteAttachmentRequest,
    db_session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """删除当前会话工作区中的附件"""
    user_id = request.state.payload.sub

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, body.conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFoundError

    # 获取工作区目录
    workspace_dir = get_workspace_dir(user_id, body.conversation_id)
    # 获取附件文件路径
    target_path = _build_attachment_path(workspace_dir, body.f_path)
    # 删除文件
    if target_path.exists() and target_path.is_file():
        target_path.unlink()

    logger.info(f"Delete attachment: conversation_id={body.conversation_id}, file={body.f_path}")


@router.get("/get")
async def api_get_attachment(
    request: Request,
    conversation_id: int,
    f_path: str,
    db_session: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    """获取当前会话工作区中的附件文件"""
    user_id = request.state.payload.sub

    # 检查对话是否存在且属于当前用户
    conversation = await conversation_repo.get_by_id(db_session, conversation_id)
    if (conversation is None) or (conversation.user_id != user_id):
        raise chat_error.ConversationNotFoundError

    # 获取工作区目录
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    # 获取附件文件路径
    target_path = _build_attachment_path(workspace_dir, f_path)
    # 文件不存在则报错
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")

    # 获取文件 MIME 类型
    media_type, _ = mimetypes.guess_type(target_path.name)

    logger.info(f"Get attachment: {conversation_id=}, file={f_path}")
    return FileResponse(
        path=target_path,
        media_type=media_type or "application/octet-stream",
        filename=target_path.name,
    )
