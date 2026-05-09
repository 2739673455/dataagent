"""安全相关的基础设施实现"""

import base64
import hashlib
import re
import secrets

from pwdlib._hash import PasswordHash as PwdLibHash

from app.domain.ports import PasswordHasher, PkceService, TokenFactory


class TokenFactoryImpl(TokenFactory):
    """令牌生成工厂 — secrets 实现"""

    def session_id(self) -> str:
        """生成 32 字节 URL 安全的会话 ID"""
        return secrets.token_urlsafe(32)

    def authorization_code(self) -> str:
        """生成 OAuth 授权码"""
        return secrets.token_urlsafe(32)

    def access_token(self) -> str:
        """生成访问令牌"""
        return secrets.token_urlsafe(32)

    def verification_code(self) -> str:
        """生成 6 位数字验证码"""
        return f"{secrets.randbelow(1_000_000):06d}"


class PasswordHasherImpl(PasswordHasher):
    """密码哈希 — pwdlib 实现"""

    def __init__(self) -> None:
        self._hasher = PwdLibHash.recommended()

    def hash(self, password: str) -> str:
        """哈希密码"""
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        """验证密码"""
        return self._hasher.verify(password, password_hash)


class PkceServiceImpl(PkceService):
    """PKCE S256 实现"""

    def validate_base64url_43(self, value: str) -> bool:
        """校验 43 字符 Base64URL 格式"""
        return re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None

    def create_code_challenge(self, code_verifier: str) -> str:
        """由 code_verifier 生成 SHA256 code_challenge"""
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
