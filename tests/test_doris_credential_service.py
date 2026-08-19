"""Doris 查询凭据加密测试"""

import unittest

from cryptography.fernet import Fernet

from app.services.doris_credential_service import (
    DorisCredentialCipher,
    DorisCredentialError,
)


class DorisCredentialCipherTest(unittest.TestCase):
    def test_password_round_trip_uses_nondeterministic_ciphertext(self) -> None:
        cipher = DorisCredentialCipher(Fernet.generate_key().decode())

        first = cipher.encrypt("query-password")
        second = cipher.encrypt("query-password")

        self.assertNotEqual(first, second)
        self.assertEqual(cipher.decrypt(first), "query-password")

    def test_wrong_deployment_key_fails_closed(self) -> None:
        encrypted = DorisCredentialCipher(Fernet.generate_key().decode()).encrypt(
            "query-password"
        )

        with self.assertRaises(DorisCredentialError):
            DorisCredentialCipher(Fernet.generate_key().decode()).decrypt(encrypted)

    def test_generated_password_is_nonempty_and_ascii(self) -> None:
        password = DorisCredentialCipher.generate_password()

        self.assertGreaterEqual(len(password), 40)
        self.assertTrue(password.isascii())


if __name__ == "__main__":
    unittest.main()
