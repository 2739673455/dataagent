from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions.user import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    UserDisabledError,
    UserEmailSameError,
    UserError,
    UserNameSameError,
    UserNotFoundError,
    UserPasswordSameError,
)
from app.utils.log import auth_logger


def register_user_exception_handlers(app):
    @app.exception_handler(UserError)
    async def user_error_handler(request: Request, exc: UserError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(EmailAlreadyExistsError)
    async def email_already_exists_handler(
        request: Request, exc: EmailAlreadyExistsError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UserNotFoundError)
    async def user_not_found_handler(request: Request, exc: UserNotFoundError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UserDisabledError)
    async def user_disabled_handler(request: Request, exc: UserDisabledError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidCredentialsError)
    async def invalid_credentials_handler(
        request: Request, exc: InvalidCredentialsError
    ):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UserNameSameError)
    async def user_name_same_handler(request: Request, exc: UserNameSameError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UserEmailSameError)
    async def user_email_same_handler(request: Request, exc: UserEmailSameError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(UserPasswordSameError)
    async def user_password_same_handler(request: Request, exc: UserPasswordSameError):
        auth_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )
