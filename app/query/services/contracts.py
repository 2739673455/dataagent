"""查询经验服务依赖协议"""

from typing import Protocol
from uuid import UUID

from app.identity.models import DorisQueryIdentity, User


class QueryExperienceIndexScheduler(Protocol):
    """查询经验索引任务调度协议"""

    def enqueue(self, experience_id: UUID, revision: int) -> None:
        """提交指定经验版本的索引同步任务"""
        ...


class QueryPrincipalUserProvider(Protocol):
    """查询身份解析所需的用户读取接口"""

    async def get_user_by_id(self, user_id: int) -> User | None:
        """按主键读取查询发起用户"""
        ...


class QueryIdentityProvider(Protocol):
    """查询身份解析所需的角色读取接口"""

    async def get(self, role_name: str) -> DorisQueryIdentity | None:
        """读取 Doris 角色对应的稳定查询身份"""
        ...


class QueryCredentialDecryptor(Protocol):
    """查询身份解析所需的凭据解密能力"""

    def decrypt(self, encrypted_password: str) -> str:
        """解密 Doris 查询用户密码"""
        ...
