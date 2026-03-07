from fastapi import APIRouter

from . import chat, conversation

router = APIRouter(prefix="/api")

router.include_router(conversation.router)
router.include_router(chat.router)
