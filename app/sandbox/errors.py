"""沙箱运行时异常。"""


class SandboxPathError(ValueError):
    """沙箱路径非法。"""


class SandboxFileTooLargeError(OSError):
    """沙箱文件超过大小限制。"""


class SandboxDeletedError(RuntimeError):
    """沙箱资源已被删除。"""


class SandboxCapacityError(RuntimeError):
    """沙箱运行容量不可用。"""


class SandboxCapacityUnavailableError(SandboxCapacityError):
    """运行容器已满且没有可回收的空闲容器。"""


class SandboxOwnershipError(RuntimeError):
    """沙箱跨进程所有权不可用。"""
