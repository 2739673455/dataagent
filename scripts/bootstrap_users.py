"""初始化预定义用户及 Doris 查询角色，可重复执行。"""

import argparse
import secrets
from dataclasses import dataclass

from app.shared.async_runtime import run_async


@dataclass(frozen=True, slots=True)
class RolePreset:
    name: str
    description: str
    query_user: str
    workload_group: str = "normal"


@dataclass(frozen=True, slots=True)
class UserPreset:
    id: int
    username: str
    role_name: str


# Doris 内置 admin 是全局管理员，业务查询使用独立角色。
ROLES = (
    RolePreset("dataagent_admin", "业务库全部表的查询权限", "dataagent_admin_query"),
)
USERS = (UserPreset(1, "admin", "dataagent_admin"),)


async def bootstrap() -> None:
    from sqlalchemy.dialects.postgresql import insert

    from app.identity.models.account import User
    from app.identity.models.doris import DorisQueryIdentity
    from app.identity.repositories.doris_role import DorisRoleRepository
    from app.identity.services.credential import DorisCredentialCipher
    from app.shared.clients.doris_client_manager import DorisClientManager
    from app.shared.clients.postgres_client_manager import PostgresClientManager
    from app.shared.config.app_config import cfg
    from app.shared.database.base import AuthBase

    postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
    doris = DorisClientManager(cfg.doris)
    cipher = DorisCredentialCipher(
        cfg.doris_credentials.encryption_key.get_secret_value()
    )
    try:
        postgres.init()
        doris.init()
        await postgres.init_tables()
        repository = DorisRoleRepository(doris)
        for role in ROLES:
            # 先保存随机凭据，Doris 初始化失败后重试沿用同一密码。
            async with postgres.session() as session, session.begin():
                await session.execute(
                    insert(DorisQueryIdentity)
                    .values(
                        role_name=role.name,
                        description=role.description,
                        query_user=role.query_user,
                        encrypted_password=cipher.encrypt(secrets.token_urlsafe(32)),
                        workload_group=role.workload_group,
                    )
                    .on_conflict_do_update(
                        index_elements=[DorisQueryIdentity.role_name],
                        set_={
                            "description": role.description,
                            "query_user": role.query_user,
                            "workload_group": role.workload_group,
                        },
                    )
                )
                identity = await session.get(DorisQueryIdentity, role.name)
                assert identity is not None
                password = cipher.decrypt(identity.encrypted_password)
            await repository.ensure_role_identity(
                role_name=role.name,
                query_user=role.query_user,
                password=password,
                workload_group=role.workload_group,
                database=cfg.doris.database,
            )
            print(f"角色已就绪: {role.name}，查询范围: {cfg.doris.database}.*")

        # 全部角色初始化成功后才发布用户，避免前端选择到未就绪的新用户。
        async with postgres.session() as session, session.begin():
            for user in USERS:
                await session.execute(
                    insert(User)
                    .values(
                        id=user.id,
                        username=user.username,
                        doris_role_name=user.role_name,
                    )
                    .on_conflict_do_update(
                        index_elements=[User.id],
                        set_={
                            "username": user.username,
                            "doris_role_name": user.role_name,
                        },
                    )
                )
                print(f"预定义用户: {user.username}，角色: {user.role_name}")
    finally:
        await doris.close()
        await postgres.close()


if __name__ == "__main__":
    # 帮助信息不依赖应用配置或数据库。
    argparse.ArgumentParser(description=__doc__).parse_args()
    run_async(bootstrap())
