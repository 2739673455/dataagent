"""身份认证、授权策略和账号注销的公开入口。"""

from app.identity.accounts.deletion import UserDeletionStateService
from app.identity.service import IdentityService

__all__ = ["IdentityService", "UserDeletionStateService"]
