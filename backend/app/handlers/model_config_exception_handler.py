from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions.model_config import ModelConfigError, ModelConfigNotFoundError
from app.utils.log import app_logger


def register_model_config_exception_handlers(app):
    @app.exception_handler(ModelConfigError)
    async def model_config_error_handler(request: Request, exc: ModelConfigError):
        app_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(ModelConfigNotFoundError)
    async def model_config_not_found_handler(
        request: Request, exc: ModelConfigNotFoundError
    ):
        app_logger.error(exc)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )
