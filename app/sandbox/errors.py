"""沙箱运行时异常。"""

from http import HTTPStatus

from app.shared.errors.base import ProblemError


class SandboxPathError(ProblemError):
    """沙箱路径非法。"""

    type = "sandbox-path-invalid"
    title = "路径非法"
    status = HTTPStatus.FORBIDDEN


class SandboxFileTooLargeError(ProblemError):
    """沙箱文件超过大小限制。"""

    type = "sandbox-file-too-large"
    title = "文件过大"
    status = HTTPStatus.CONTENT_TOO_LARGE


class SandboxDeletedError(RuntimeError):
    """沙箱资源已被删除。"""


class SandboxCapacityUnavailableError(RuntimeError):
    """运行容器已满且没有可回收的空闲容器。"""


class SandboxOwnershipError(RuntimeError):
    """沙箱跨进程所有权不可用。"""
