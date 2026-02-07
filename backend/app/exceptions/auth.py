"""认证异常"""


class AuthenticationError(Exception): ...


class InvalidAccessTokenError(AuthenticationError):
    def __init__(self, message: str = "无效访问令牌"):
        super().__init__(message)


class ExpiredAccessTokenError(AuthenticationError):
    def __init__(self, message: str = "访问令牌过期"):
        super().__init__(message)


class InvalidRefreshTokenError(AuthenticationError):
    def __init__(self, message: str = "无效刷新令牌"):
        super().__init__(message)


class ExpiredRefreshTokenError(AuthenticationError):
    def __init__(self, message: str = "刷新令牌过期"):
        super().__init__(message)


class InsufficientPermissionsError(AuthenticationError):
    def __init__(self, message: str = "权限不足"):
        super().__init__(message)
