"""初始化预定义用户及 Doris 查询角色，可重复执行。"""

import argparse
import secrets
from contextlib import AsyncExitStack
from dataclasses import dataclass

from app.shared.async_runtime import run_async


@dataclass(frozen=True, slots=True)
class RolePreset:
    name: str
    description: str
    query_user: str
    workload_group: str = "normal"
    tables: tuple[str, ...] = ("*",)


@dataclass(frozen=True, slots=True)
class UserPreset:
    id: int
    username: str
    role_name: str


# Doris 内置 admin 是全局管理员，业务查询使用独立角色。
ROLES = (
    RolePreset("dataagent_admin", "业务库全部表的查询权限", "dataagent_admin_query"),
    RolePreset(
        "dataagent_growth",
        "增长运营：渠道流量、浏览转化、加购收藏与优惠券触达",
        "dataagent_growth_query",
        tables=(
            "dim_date",
            "dim_channel_info",
            "dim_page_info",
            "dim_category_info_zip",
            "dim_brand_info",
            "dim_spu_info_zip",
            "dim_sku_info_zip",
            "dim_coupon_template_version",
            "bridge_coupon_scope",
            "dwd_traffic_session_di",
            "dwd_traffic_page_view_di",
            "dwd_traffic_search_di",
            "dwd_traffic_search_click_di",
            "dwd_interaction_cart_event_di",
            "dwd_interaction_favor_event_di",
            "dwd_marketing_user_coupon_event_di",
        ),
    ),
    RolePreset(
        "dataagent_supply_chain",
        "供应链：商品仓储、库存变动、物流履约与配送效率",
        "dataagent_supply_chain_query",
        tables=(
            "dim_date",
            "dim_geo_region_zip",
            "dim_seller_info_zip",
            "dim_shop_info_zip",
            "dim_category_info_zip",
            "dim_brand_info",
            "dim_spu_info_zip",
            "dim_sku_info_zip",
            "dim_warehouse_info_zip",
            "dim_logistics_company",
            "dwd_trade_delivery_di",
            "dwd_trade_delivery_item_di",
            "dwd_trade_delivery_status_event_di",
            "dwd_inventory_change_di",
            "dwd_inventory_daily_snapshot_df",
        ),
    ),
)
USERS = (
    UserPreset(1, "admin", "dataagent_admin"),
    UserPreset(2, "growth", "dataagent_growth"),
    UserPreset(3, "supply_chain", "dataagent_supply_chain"),
)


async def bootstrap() -> None:
    from sqlalchemy.dialects.postgresql import insert

    from app.identity.models.account import User
    from app.identity.models.doris import DorisQueryIdentity
    from app.identity.services.credential import DorisCredentialCipher
    from app.shared.clients.doris_client_manager import DorisClientManager
    from app.shared.clients.postgres_client_manager import PostgresClientManager
    from app.shared.config.app_config import cfg
    from app.shared.database.base import AuthBase

    async with AsyncExitStack() as stack:
        postgres = PostgresClientManager(cfg.auth_postgresql, AuthBase)
        stack.push_async_callback(postgres.close)
        doris = DorisClientManager(cfg.doris)
        stack.push_async_callback(doris.close)
        cipher = DorisCredentialCipher(
            cfg.doris_credentials.encryption_key.get_secret_value()
        )
        await postgres.init_tables()
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
            async with doris.engine.connect() as connection:
                quote = connection.dialect.identifier_preparer.quote_identifier
                role_sql = quote(role.name)
                group_sql = quote(role.workload_group)
                database_sql = quote(cfg.doris.database)
                await connection.exec_driver_sql(
                    f"CREATE ROLE IF NOT EXISTS {role_sql}", ()
                )
                for table in role.tables:
                    table_sql = "*" if table == "*" else quote(table)
                    await connection.exec_driver_sql(
                        f"GRANT SELECT_PRIV ON `internal`.{database_sql}.{table_sql} TO ROLE {role_sql}",
                        (),
                    )
                await connection.exec_driver_sql(
                    f"GRANT USAGE_PRIV ON WORKLOAD GROUP {group_sql} TO ROLE {role_sql}",
                    (),
                )
                await connection.exec_driver_sql(
                    "CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s DEFAULT ROLE %s",
                    (role.query_user, "%", password, role.name),
                )
                # 将 Doris 查询账号的密码设为身份库中保存的值。
                await connection.exec_driver_sql(
                    "SET PASSWORD FOR %s@%s = PASSWORD(%s)",
                    (role.query_user, "%", password),
                )
                await connection.exec_driver_sql(
                    f"GRANT {role_sql} TO %s@%s", (role.query_user, "%")
                )
            scope = (
                "全部表" if role.tables == ("*",) else f"{len(role.tables)} 张业务表"
            )
            print(f"角色已就绪: {role.name}，查询范围: {cfg.doris.database}（{scope}）")

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


if __name__ == "__main__":
    # 帮助信息不依赖应用配置或数据库。
    argparse.ArgumentParser(description=__doc__).parse_args()
    run_async(bootstrap())
