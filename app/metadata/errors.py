"""元数据业务错误"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class InvalidMetadataError(ProblemError):
    """表示元数据内容未通过业务校验"""

    type = "invalid-metadata"
    title = "元数据校验失败"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class MetadataNotFoundError(ProblemError):
    """表示目标元数据资源不存在"""

    type = "metadata-not-found"
    title = "元数据不存在"
    status = HTTPStatus.NOT_FOUND


class MetadataConflictError(ProblemError):
    """表示元数据变更与现有状态冲突"""

    type = "metadata-conflict"
    title = "元数据冲突"
    status = HTTPStatus.CONFLICT
