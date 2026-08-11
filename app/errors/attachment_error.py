from http import HTTPStatus

from app.errors.base import ProblemError


class PathTraversalError(ProblemError):
    type = "path-traversal"
    title = "路径穿越"
    status = HTTPStatus.FORBIDDEN


class AttachmentNotFoundError(ProblemError):
    type = "attachment-not-found"
    title = "附件不存在"
    status = HTTPStatus.NOT_FOUND


class AttachmentTooLargeError(ProblemError):
    type = "attachment-too-large"
    title = "附件过大"
    status = HTTPStatus.CONTENT_TOO_LARGE


class SandboxStorageLimitError(ProblemError):
    type = "sandbox-storage-limit"
    title = "沙盒存储空间不足"
    status = HTTPStatus.INSUFFICIENT_STORAGE
