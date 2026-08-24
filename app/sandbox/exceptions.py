"""沙箱运行时异常"""


class SandboxPathError(ValueError):
    """沙箱路径非法"""


class SandboxFileTooLargeError(OSError):
    """沙箱文件超过大小限制"""


class SandboxStorageLimitError(OSError):
    """沙箱工作区超过容量限制"""


class SandboxDeletedError(RuntimeError):
    """沙箱资源已被删除"""


class SandboxCapacityError(RuntimeError):
    """沙箱运行容量不可用"""


class SandboxCapacityTimeoutError(SandboxCapacityError):
    """等待沙箱运行容量超时"""


class SandboxCapacityQueueFullError(SandboxCapacityError):
    """沙箱容量等待队列已满"""


class SandboxCapacityClosedError(SandboxCapacityError):
    """沙箱容量调度器已关闭"""


class SandboxCapacityCancelledError(SandboxCapacityError):
    """沙箱容量等待已取消"""


class SandboxOwnershipError(RuntimeError):
    """沙箱跨进程所有权不可用"""
