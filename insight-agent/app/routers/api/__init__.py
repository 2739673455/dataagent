from fastapi import APIRouter

from . import admin, chat

router = APIRouter(prefix="/api")

router.include_router(admin.router)
router.include_router(chat.router)
