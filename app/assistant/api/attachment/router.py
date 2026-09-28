"""会话产物下载路由。"""

import mimetypes
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from app.assistant.api.attachment.dependencies import AttachmentServiceDep
from app.identity.api.dependencies import CurrentUserDep

router = APIRouter(tags=["attachment"])


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
    media_type, _ = mimetypes.guess_type(f_path)
    return Response(
        content=content,
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(f_path.rsplit('/', 1)[-1])}"
            )
        },
    )
