from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions.auth import (
    ExpiredAccessTokenError,
    ExpiredRefreshTokenError,
    InsufficientPermissionsError,
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
)
from app.utils.log import auth_logger


def register_auth_exception_handlers(app):
    @app.exception_handler(InvalidAccessTokenError)
    async def invalid_access_token_handler(
        request: Request, exc: InvalidAccessTokenError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ExpiredAccessTokenError)
    async def expired_access_token_handler(
        request: Request, exc: ExpiredAccessTokenError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidRefreshTokenError)
    async def invalid_refresh_token_handler(
        request: Request, exc: InvalidRefreshTokenError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ExpiredRefreshTokenError)
    async def expired_refresh_token_handler(
        request: Request, exc: ExpiredRefreshTokenError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InsufficientPermissionsError)
    async def insufficient_permissions_handler(
        request: Request, exc: InsufficientPermissionsError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )
