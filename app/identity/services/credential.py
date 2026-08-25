"""Doris 查询身份凭据加密"""

import secrets

from cryptography.fernet import Fernet, InvalidToken


class DorisCredentialError(RuntimeError):
    """Doris 查询凭据无法解密"""


class DorisCredentialCipher:
    """使用部署级密钥加密 Doris 查询密码"""

    def __init__(self, encryption_key: str) -> None:
        """使用部署级 Fernet 密钥初始化凭据加密器"""
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Doris 凭据加密主密钥格式无效") from exc

    def encrypt(self, password: str) -> str:
        """加密 Doris 查询密码"""
        if not password:
            raise ValueError("Doris 查询密码不能为空")
        return self._fernet.encrypt(password.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_password: str) -> str:
        """解密 Doris 查询密码"""
        try:
            return self._fernet.decrypt(encrypted_password.encode("ascii")).decode(
                "utf-8"
            )
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise DorisCredentialError("Doris 查询凭据解密失败") from exc

    @staticmethod
    def generate_password() -> str:
        """生成仅供服务端保存的随机 Doris 查询密码"""
        return secrets.token_urlsafe(36)
