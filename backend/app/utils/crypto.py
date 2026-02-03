from app.config import CFG
from cryptography.fernet import Fernet

_fernet = Fernet(CFG.encryption_key.encode())  # 初始化加密器


def encrypt(plaintext: str | None) -> str | None:
    """加密明文字符串"""
    if not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """解密密文字符串"""
    if not ciphertext:
        return ciphertext
    return _fernet.decrypt(ciphertext.encode()).decode()
