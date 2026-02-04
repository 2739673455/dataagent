"""用户异常"""


class UserError(Exception): ...


class EmailAlreadyExistsError(UserError):
    def __init__(self, message: str = "邮箱已被注册"):
        super().__init__(message)


class UserNotFoundError(UserError):
    def __init__(self, message: str = "用户不存在"):
        super().__init__(message)


class UserDisabledError(UserError):
    def __init__(self, message: str = "用户已被禁用"):
        super().__init__(message)


class InvalidCredentialsError(UserError):
    def __init__(self, message: str = "密码错误"):
        super().__init__(message)


class UserNameSameError(UserError):
    def __init__(self, message: str = "用户名与原用户名相同"):
        super().__init__(message)


class UserEmailSameError(UserError):
    def __init__(self, message: str = "邮箱与原邮箱相同"):
        super().__init__(message)


class UserPasswordSameError(UserError):
    def __init__(self, message: str = "密码与原密码相同"):
        super().__init__(message)
