from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.conversation import (
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    DeleteConversationRequest,
    UpdateConversationRequest,
)
from app.schemas.user import AccessTokenPayload
from app.services.auth import authenticate_access_token
from app.services.conversation import (
    create_conversation,
    delete_conversations,
    get_conversations,
    update_conversation_data,
)
from app.services.database import get_app_db
from app.utils.log import app_logger

router = APIRouter(prefix="/conversation", tags=["对话管理"])


@router.get("", response_model=ConversationListResponse)
async def api_get_conversations(
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> ConversationListResponse:
    """获取对话列表"""
    conversations = await get_conversations(db_session, payload.sub)
    app_logger.info(f"User get conversations: {[i.id for i in conversations]}")
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                conversation_id=i.id,
                title=i.title,
                update_at=i.update_at,
                model_config_id=i.model_config_id,
            )
            for i in conversations
        ]
    )


@router.post(
    "/create", status_code=status.HTTP_201_CREATED, response_model=ConversationResponse
)
async def api_create_conversation(
    request: CreateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
) -> ConversationResponse:
    """创建新对话"""
    conversation = await create_conversation(
        db_session, payload.sub, request.model_config_id
    )
    app_logger.info(f"User create conversation: {conversation.id}")
    return ConversationResponse(
        conversation_id=conversation.id,
        title=None,
        update_at=conversation.update_at,
        model_config_id=conversation.model_config_id,
    )


@router.post("/update")
async def api_update_conversation(
    request: UpdateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """修改对话信息"""
    app_logger.info(
        f"User update conversation: conversation={request.conversation_id}, model_config_id={request.model_config_id}"
    )
    conversation_data = {}
    if request.title is not None:
        conversation_data["title"] = request.title
    if request.model_config_id is not None:
        conversation_data["model_config_id"] = request.model_config_id
    await update_conversation_data(
        db_session, request.conversation_id, conversation_data
    )


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_conversations(
    request: DeleteConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
    payload: Annotated[AccessTokenPayload, Depends(authenticate_access_token)],
):
    """删除对话"""
    app_logger.info(f"User delete conversations: {request.ids}")
    await delete_conversations(db_session, request.ids)
