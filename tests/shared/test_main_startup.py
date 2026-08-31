"""应用启动安全校验行为测试。"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import main


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *args: object) -> None:
        return None


class DorisStartupVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_identity_drift_warns_without_blocking_startup(self) -> None:
        identity = MagicMock(
            role_name="sales",
            query_user="sales_query",
            encrypted_password="encrypted",
            workload_group="sales_group",
        )
        identity_repo = MagicMock()
        identity_repo.list_all = AsyncMock(return_value=[identity])
        role_repo = MagicMock()
        role_repo.verify_configured_roles = AsyncMock(
            side_effect=RuntimeError("missing role")
        )
        query_repo = MagicMock()
        query_repo.verify_readonly_access = AsyncMock(
            side_effect=RuntimeError("missing select grant")
        )
        with (
            patch.object(
                main.auth_postgres_client_manager,
                "session",
                return_value=_AsyncContext(MagicMock()),
            ),
            patch.object(main, "DorisQueryIdentityPGRepo", return_value=identity_repo),
            patch.object(main, "DorisRoleRepository", return_value=role_repo),
            patch.object(main, "DorisQueryRepository", return_value=query_repo),
            patch.object(
                main.query_doris_client_registry,
                "get_or_create",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch.object(main.DorisCredentialCipher, "decrypt", return_value="secret"),
            patch.object(main.logger, "warning") as warning,
        ):
            await main._verify_doris_query_identities()

        self.assertEqual(warning.call_count, 2)
        self.assertTrue(
            all("应用继续启动" in call.args[0] for call in warning.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
