"""读取 Doris 的实际权限并初始化预定义角色。"""

import re

from sqlalchemy import text

from app.identity.models.doris import DorisAuthorizationSnapshot
from app.identity.repositories.doris_authorization import parse_authorization
from app.shared.clients.doris_client_manager import DorisClientManager
from app.shared.contracts.doris import DORIS_IDENTIFIER_PATTERN


class DorisRoleRepository:
    """使用管理连接读取权限，初始化专用查询账号。"""

    def __init__(self, provider: DorisClientManager) -> None:
        """绑定 Doris 管理连接提供器。"""
        self._provider = provider

    async def read_authorization(
        self,
        *,
        role_name: str,
        query_user: str,
        data_source: str,
        catalog: str,
        database: str,
    ) -> DorisAuthorizationSnapshot:
        """读取查询账号有效权限，解析为业务库权限快照。"""
        user = self._quote_user(query_user)
        async with self._provider.connection() as connection:
            await connection.exec_driver_sql("SET show_user_default_role = false")
            result = await connection.execute(text(f"SHOW GRANTS FOR {user}@'%'"))
            rows = result.mappings().all()
            if len(rows) != 1:
                raise ValueError("Doris 查询账号授权结果必须唯一")
            return parse_authorization(
                dict(rows[0]),
                role_name=role_name,
                query_user=query_user,
                data_source=data_source,
                catalog=catalog,
                database=database,
            )

    async def ensure_role_identity(
        self,
        *,
        role_name: str,
        query_user: str,
        password: str,
        workload_group: str,
        database: str,
    ) -> None:
        """初始化角色和查询账号，授予业务库 SELECT 及工作组使用权限。"""
        role = self._quote_identifier(role_name)
        user = self._quote_user(query_user)
        group = self._quote_identifier(workload_group)
        database_sql = self._quote_identifier(database)
        if re.fullmatch(r"[A-Za-z0-9_-]+", password) is None:
            raise ValueError("生成的 Doris 密码格式无效")
        async with self._provider.connection() as connection:
            await connection.exec_driver_sql(f"CREATE ROLE IF NOT EXISTS {role}")
            await connection.exec_driver_sql(
                f"GRANT SELECT_PRIV ON `internal`.{database_sql}.* TO ROLE {role}"
            )
            await connection.exec_driver_sql(
                f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group} TO ROLE {role}"
            )
            await connection.exec_driver_sql(
                f"CREATE USER IF NOT EXISTS {user}@'%' IDENTIFIED BY '{password}' "
                f"DEFAULT ROLE '{role_name}'"
            )
            # 将 Doris 查询账号的密码设为身份库中保存的值。
            await connection.exec_driver_sql(
                f"SET PASSWORD FOR {user}@'%' = PASSWORD('{password}')"
            )
            await connection.exec_driver_sql(f"GRANT {role} TO {user}@'%'")

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """校验并引用 Doris 标识符。"""
        if re.fullmatch(DORIS_IDENTIFIER_PATTERN, identifier) is None:
            raise ValueError("Doris 标识符无效")
        return f"`{identifier}`"

    @staticmethod
    def _quote_user(user_name: str) -> str:
        """校验并引用 Doris 用户名。"""
        if re.fullmatch(DORIS_IDENTIFIER_PATTERN, user_name) is None:
            raise ValueError("Doris 用户名格式无效")
        return f"'{user_name}'"
