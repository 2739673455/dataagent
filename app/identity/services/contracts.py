"""Identity 应用服务依赖协议。"""

from typing import Protocol


class QueryClientInvalidator(Protocol):
    """Doris 角色变更所需的查询客户端失效能力。"""

    async def invalidate(self, role_name: str) -> None:
        """关闭并移除指定角色的共享查询客户端。"""
        ...
