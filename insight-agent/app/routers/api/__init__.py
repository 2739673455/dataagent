from fastapi import APIRouter

from . import chat

router = APIRouter(prefix="/api")

router.include_router(chat.router)
