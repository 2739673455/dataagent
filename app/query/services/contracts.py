"""查询经验服务依赖协议"""

from typing import Protocol
from uuid import UUID


class QueryExperienceIndexScheduler(Protocol):
    """查询经验索引任务调度协议"""

    def enqueue(self, experience_id: UUID, revision: int) -> None:
        """提交指定经验版本的索引同步任务"""
        ...

