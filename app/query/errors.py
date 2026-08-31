"""查询模块业务错误。"""

from http import HTTPStatus

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
