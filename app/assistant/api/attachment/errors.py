"""附件接口错误"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class PathTraversalError(ProblemError):
    """表示请求路径越过允许的沙箱边界"""

    type = "path-traversal"
    title = "路径穿越"
    status = HTTPStatus.FORBIDDEN


class AttachmentNotFoundError(ProblemError):
    """表示目标沙箱附件不存在"""

    type = "attachment-not-found"
    title = "附件不存在"
    status = HTTPStatus.NOT_FOUND


class AttachmentTooLargeError(ProblemError):
    """表示附件大小超过上传限制"""

    type = "attachment-too-large"
    title = "附件过大"
    status = HTTPStatus.CONTENT_TOO_LARGE


class SandboxStorageLimitProblem(ProblemError):
    """表示沙箱工作区存储空间不足"""

    type = "sandbox-storage-limit"
    title = "沙箱存储空间不足"
    status = HTTPStatus.INSUFFICIENT_STORAGE
