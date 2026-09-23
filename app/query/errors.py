"""查询模块业务错误。"""

from http import HTTPStatus

from app.query.models.execution import QueryExecutionStatus
from app.query.models.validation import QueryValidationResult
from app.shared.errors.base import ProblemError


class QueryExperienceNotFoundError(ProblemError):
    """表示目标查询经验不存在。"""

    type = "query-experience-not-found"
    title = "查询经验不存在"
    status = HTTPStatus.NOT_FOUND


class QueryExperienceStateConflictError(ProblemError):
    """表示查询经验当前状态不允许执行管理操作。"""

    type = "query-experience-state-conflict"
    title = "查询经验状态冲突"
    status = HTTPStatus.CONFLICT


class QueryRejectedError(ValueError):
    """SQL 未通过确定性安全校验。"""

    def __init__(self, result: QueryValidationResult) -> None:
        """保存完整校验结果并汇总拒绝原因。"""
        self.result = result
        message = "; ".join(issue.message for issue in result.issues)
        super().__init__(message or "SQL 查询已被拒绝")


class QueryResultShapeError(RuntimeError):
    """数据库返回的结果结构不稳定或不适合文件输出。"""


class QueryExecutionTimeoutError(RuntimeError):
    """Doris 查询执行超时。"""


def classify_query_error(error: Exception) -> tuple[QueryExecutionStatus, str]:
    """供执行记录和工具协议共用的查询失败分类。"""
    if isinstance(error, QueryRejectedError):
        return "rejected", "sql_validation_failed"
    if isinstance(error, QueryExecutionTimeoutError):
        return "failed", "query_timeout"
    if isinstance(error, QueryResultShapeError):
        return "failed", "query_result_invalid"
    return "failed", "readonly_query_failed"
