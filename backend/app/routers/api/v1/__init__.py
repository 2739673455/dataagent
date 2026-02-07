from fastapi import APIRouter

from . import chat, conversation, model_config, user

router = APIRouter(prefix="/v1")
router.include_router(user.router)
router.include_router(model_config.router)
router.include_router(conversation.router)
router.include_router(chat.router)
