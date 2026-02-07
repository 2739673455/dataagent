"""模型配置异常"""


class ModelConfigError(Exception): ...


class ModelConfigNotFoundError(ModelConfigError):
    def __init__(self, message: str = "模型配置不存在"):
        super().__init__(message)
