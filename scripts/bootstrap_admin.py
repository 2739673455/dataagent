"""使用显式环境凭据引导平台管理员"""

import asyncio
import os

from app.clients.postgres_client_manager import meta_postgres_client_manager
from app.conf.app_config import cfg
from app.repositories.auth_pg_repo import AuthPGRepo
from app.services.auth_service import Argon2PasswordManager, AuthService

_USERNAME_ENV = "DATAAGENT_BOOTSTRAP_ADMIN_USERNAME"
_EMAIL_ENV = "DATAAGENT_BOOTSTRAP_ADMIN_EMAIL"
_PASSWORD_ENV = "DATAAGENT_BOOTSTRAP_ADMIN_PASSWORD"


def _required_environment(name: str) -> str:
    """读取必需且非空的环境变量"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


async def bootstrap_admin() -> None:
    """创建或幂等确认显式凭据对应的管理员"""
    username = _required_environment(_USERNAME_ENV)
    email = _required_environment(_EMAIL_ENV)
    password = _required_environment(_PASSWORD_ENV)
    meta_postgres_client_manager.init()
    try:
        await meta_postgres_client_manager.init_tables()
        async with meta_postgres_client_manager.session() as session:
            result = await AuthService(
                AuthPGRepo(session),
                cfg.auth,
                Argon2PasswordManager(),
            ).bootstrap_admin(username, email, password)
        outcome = "created" if result.created else "verified"
        grant = "granted" if result.admin_granted else "already-present"
        print(
            f"Admin bootstrap {outcome}: user_id={result.user.id}, "
            f"admin_role={grant}"
        )
    finally:
        await meta_postgres_client_manager.close()


if __name__ == "__main__":
    asyncio.run(bootstrap_admin())
