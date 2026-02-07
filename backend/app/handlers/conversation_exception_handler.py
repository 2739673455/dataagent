from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions.conversation import ConversationError, ConversationNotFoundError
from app.utils.log import app_logger


def register_conversation_exception_handlers(app):
    @app.exception_handler(ConversationError)
    async def conversation_error_handler(request: Request, exc: ConversationError):
        app_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found_handler(
        request: Request, exc: ConversationNotFoundError
    ):
        app_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
