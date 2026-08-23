"""使用 CLI 参数或环境变量引导平台管理员"""

import argparse
import asyncio
import os

_USERNAME_ENV = "ADMIN_USERNAME"
_EMAIL_ENV = "ADMIN_EMAIL"
_PASSWORD_ENV = "ADMIN_PASSWORD"


def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="DataAgent 平台初始管理员引导工具",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=32),
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")
    parser.add_argument("-u", "--username", help="管理员用户名", default=None)
    parser.add_argument("-e", "--email", help="管理员邮箱", default=None)
    parser.add_argument("-p", "--password", help="管理员初始密码", default=None)
    return parser.parse_args()


def _resolve_value(cli_val: str | None, env_name: str) -> str:
    """按 CLI -> 环境变量 的优先级获取非空配置值"""
    if cli_val and cli_val.strip():
        return cli_val.strip()
    env_val = os.environ.get(env_name, "").strip()
    if env_val:
        return env_val
    raise ValueError(f"缺少必要配置，请通过 CLI 参数或环境变量 {env_name} 提供")


async def bootstrap_admin() -> None:
    """创建或幂等确认显式凭据对应的管理员"""
    args = _parse_args()
    username = _resolve_value(args.username, _USERNAME_ENV)
    email = _resolve_value(args.email, _EMAIL_ENV)
    password = _resolve_value(args.password, _PASSWORD_ENV)

    from app.identity.repositories.auth import AuthPGRepo
    from app.identity.services.auth import Argon2PasswordManager, AuthService
    from app.shared.clients.postgres_client_manager import auth_postgres_client_manager
    from app.shared.config.app_config import cfg

    auth_postgres_client_manager.init()
    try:
        await auth_postgres_client_manager.init_tables()
        async with auth_postgres_client_manager.session() as session:
            result = await AuthService(
                AuthPGRepo(session),
                cfg.auth,
                Argon2PasswordManager(),
            ).bootstrap_admin(username, email, password)
        outcome = "created" if result.created else "verified"
        grant = "granted" if result.admin_granted else "already-present"
        print(
            f"Admin bootstrap {outcome}: user_id={result.user.id}, "
            f"administrator={grant}, doris_role={result.user.doris_role_name}"
        )
    finally:
        await auth_postgres_client_manager.close()


if __name__ == "__main__":
    asyncio.run(bootstrap_admin())
