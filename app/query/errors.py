"""查询模块业务错误。"""

from app.query.models.execution import QueryExecutionStatus, QueryExecutionTimeoutError
from app.query.models.validation import QueryValidationResult


class QueryRejectedError(ValueError):
    """SQL 未通过确定性安全校验。"""

    def __init__(self, result: QueryValidationResult) -> None:
        """保存完整校验结果并汇总拒绝原因。"""
        self.result = result
        message = "; ".join(issue.message for issue in result.issues)
        super().__init__(message or "SQL 查询已被拒绝")


class QueryResultShapeError(RuntimeError):
    """数据库返回的结果结构不稳定或不适合文件输出。"""


def classify_query_error(error: Exception) -> tuple[QueryExecutionStatus, str]:
    """供执行记录和工具协议共用的查询失败分类。"""
    if isinstance(error, QueryRejectedError):
        return "rejected", "sql_validation_failed"
    if isinstance(error, QueryExecutionTimeoutError):
        return "failed", "query_timeout"
    if isinstance(error, QueryResultShapeError):
        return "failed", "query_result_invalid"
    return "failed", "readonly_query_failed"
