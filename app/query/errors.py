"""查询模块业务错误。"""

from app.query.models.validation import QueryValidationResult


class QueryExecutionTimeoutError(RuntimeError):
    """Doris 查询执行超时。"""


class QueryPermissionDeniedError(RuntimeError):
    """当前查询账号缺少访问目标数据所需的权限。"""


class QueryRejectedError(ValueError):
    """SQL 未通过确定性安全校验。"""

    def __init__(self, result: QueryValidationResult) -> None:
        """保存完整校验结果并汇总拒绝原因。"""
        self.result = result
        message = "; ".join(result.issues)
        super().__init__(message or "SQL 查询已被拒绝")


class QueryResultShapeError(RuntimeError):
    """数据库返回的结果结构不稳定或不适合文件输出。"""
