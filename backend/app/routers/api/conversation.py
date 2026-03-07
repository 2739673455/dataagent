from typing import Annotated

from app.schemas.conversation import (
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    DeleteConversationRequest,
    UpdateConversationRequest,
)
from app.services.conversation import (
    create_conversation,
    delete_conversations,
    get_conversations,
    update_conversation_data,
)
from app.utils.log import logger
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.utils.db import get_app_db

router = APIRouter(prefix="/conversation", tags=["对话管理"])


@router.get("")
async def api_get_conversations(
    request: Request, db_session: Annotated[AsyncSession, Depends(get_app_db)]
) -> ConversationListResponse:
    """获取对话列表"""
    conversations = await get_conversations(db_session, request.state.payload.sub)
    logger.info(
        f"User get conversations: conversation_ids={[i.id for i in conversations]}"
    )
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                conversation_id=i.id,
                title=i.title,
                update_at=i.update_at,
            )
            for i in conversations
        ]
    )


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def api_create_conversation(
    request: Request,
    body: CreateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> ConversationResponse:
    """创建新对话"""
    conversation = await create_conversation(
        db_session, request.state.payload.sub, body.model_config_id
    )
    logger.info(f"User create conversation: conversation_id={conversation.id}")
    return ConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        update_at=conversation.update_at,
    )


@router.post("/update", status_code=status.HTTP_202_ACCEPTED)
async def api_update_conversation(
    body: UpdateConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """修改对话信息"""
    logger.info(
        f"User update conversation: conversation_id={body.conversation_id}, model_config_id={body.model_config_id}, title={body.title}"
    )
    conversation_data = {"title": body.title, "model_config_id": body.model_config_id}
    await update_conversation_data(db_session, body.conversation_id, conversation_data)


@router.post("/delete")
async def api_delete_conversations(
    body: DeleteConversationRequest,
    db_session: Annotated[AsyncSession, Depends(get_app_db)],
) -> None:
    """删除对话"""
    logger.info(f"User delete conversations: conversation_ids={body.ids}")
    await delete_conversations(db_session, body.ids)
