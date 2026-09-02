"""Query 应用服务使用的依赖端口。"""

from typing import Protocol
from uuid import UUID


class QueryExperienceIndexScheduler(Protocol):
    """查询经验索引任务调度能力。"""

    def enqueue(self, experience_id: UUID, revision: int) -> bool:
        """提交指定经验版本的索引同步任务并返回是否成功。"""
        ...
