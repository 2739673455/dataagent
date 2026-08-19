"""Doris 查询身份凭据加密"""

import secrets

from cryptography.fernet import Fernet, InvalidToken


class DorisCredentialError(RuntimeError):
    """Doris 查询凭据无法解密"""


class DorisCredentialCipher:
    """使用部署级密钥加密 Doris 查询密码"""

    def __init__(self, encryption_key: str) -> None:
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("invalid Doris credential encryption key") from exc

    def encrypt(self, password: str) -> str:
        """加密 Doris 查询密码"""
        if not password:
            raise ValueError("Doris query password must not be empty")
        return self._fernet.encrypt(password.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_password: str) -> str:
        """解密 Doris 查询密码"""
        try:
            return self._fernet.decrypt(
                encrypted_password.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise DorisCredentialError("Doris query credential is invalid") from exc

    @staticmethod
    def generate_password() -> str:
        """生成仅供服务端保存的随机 Doris 查询密码"""
        return secrets.token_urlsafe(36)
