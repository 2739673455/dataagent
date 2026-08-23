"""元数据业务错误"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class InvalidMetadataError(ProblemError):
    type = "invalid-metadata"
    title = "元数据校验失败"
    status = HTTPStatus.UNPROCESSABLE_ENTITY


class MetadataNotFoundError(ProblemError):
    type = "metadata-not-found"
    title = "元数据不存在"
    status = HTTPStatus.NOT_FOUND


class MetadataConflictError(ProblemError):
    type = "metadata-conflict"
    title = "元数据冲突"
    status = HTTPStatus.CONFLICT
