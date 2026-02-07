from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions.chat import ChatError
from openai import (
    NotFoundError as OpenAINotFoundError,
    BadRequestError as OpenAIBadRequestError,
    AuthenticationError as OpenAIAuthenticationError,
    RateLimitError as OpenAIRateLimitError,
    APIError as OpenAIError,
    InternalServerError as OpenAIInternalError,
)
from app.utils.log import app_logger


def register_chat_exception_handlers(app):
    @app.exception_handler(ChatError)
    async def chat_error_handler(request: Request, exc: ChatError):
        app_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAINotFoundError)
    async def openai_not_found_handler(
        request: Request, exc: OpenAINotFoundError
    ):
        app_logger.error(f"OpenAI 404: {exc}")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAIBadRequestError)
    async def openai_bad_request_handler(
        request: Request, exc: OpenAIBadRequestError
    ):
        app_logger.error(f"OpenAI 400: {exc}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAIAuthenticationError)
    async def openai_authentication_handler(
        request: Request, exc: OpenAIAuthenticationError
    ):
        app_logger.error(f"OpenAI 401: {exc}")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAIRateLimitError)
    async def openai_rate_limit_handler(
        request: Request, exc: OpenAIRateLimitError
    ):
        app_logger.error(f"OpenAI 429: {exc}")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAIInternalError)
    async def openai_internal_handler(
        request: Request, exc: OpenAIInternalError
    ):
        app_logger.error(f"OpenAI 500: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OpenAIError)
    async def openai_error_handler(request: Request, exc: OpenAIError):
        app_logger.error(f"OpenAI error: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )