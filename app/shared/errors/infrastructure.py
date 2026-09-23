"""共享基础设施异常。"""


class AdvisoryLockBusyError(RuntimeError):
    """指定 advisory lock 已被其他执行单元占用。"""
