import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.doris_client_manager import DorisQueryClientRegistry
from app.conf.app_config import DBConfig, DorisRoleConfig


class DorisQueryClientRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_profile_reuses_data_endpoint_with_its_own_credentials(self) -> None:
        endpoint = DBConfig(
            host="doris.internal",
            port=9030,
            user="metadata_user",
            password="metadata_password",
            database="ecommerce",
        )
        profile = DorisRoleConfig(
            description="标准角色",
            is_default=True,
            query_user="standard_readonly",
            query_password="query_password",
            workload_group="dataagent_standard",
        )
        manager = MagicMock()
        manager.close = AsyncMock()
        with patch(
            "app.clients.doris_client_manager.DorisClientManager",
            return_value=manager,
        ) as manager_type:
            registry = DorisQueryClientRegistry(
                endpoint,
                {"standard_readonly": profile},
            )

        connection_config = manager_type.call_args.args[0]
        self.assertEqual(connection_config.host, endpoint.host)
        self.assertEqual(connection_config.port, endpoint.port)
        self.assertEqual(connection_config.database, endpoint.database)
        self.assertEqual(connection_config.user, profile.query_user)
        self.assertEqual(connection_config.password, profile.query_password)
        self.assertIs(registry.get("standard_readonly"), manager)

        registry.init()
        await registry.close()

        manager.init.assert_called_once_with()
        manager.close.assert_awaited_once_with()

    def test_unknown_profile_is_rejected(self) -> None:
        registry = DorisQueryClientRegistry(
            DBConfig(
                host="doris.internal",
                port=9030,
                user="metadata_user",
                password="metadata_password",
                database="ecommerce",
            ),
            {},
        )

        with self.assertRaisesRegex(RuntimeError, "not configured"):
            registry.get("missing")


if __name__ == "__main__":
    unittest.main()
