"""用户异常"""

from app.exceptions.base import AppError, BadRequestError, ConflictError


class UserError(AppError):
    code = 2000
    message = "用户服务异常"


class EmailAlreadyExistsError(ConflictError):
    code = 2001
    message = "邮箱已被注册"


class UserNotFoundError(AppError):
    code = 2002
    message = "用户不存在"
    status_code = 401


class UserDisabledError(AppError):
    code = 2003
    message = "用户已被禁用"
    status_code = 403


class InvalidCredentialsError(AppError):
    code = 2004
    message = "密码错误"
    status_code = 401


class UserNameSameError(BadRequestError):
    code = 2005
    message = "用户名与原用户名相同"


class UserEmailSameError(BadRequestError):
    code = 2006
    message = "邮箱与原邮箱相同"


class UserPasswordSameError(BadRequestError):
    code = 2007
    message = "密码与原密码相同"
