from app.exceptions.base import NotFoundError


class ModelConfigNotFoundError(NotFoundError):
    code = 1401
    message = "模型配置不存在"
