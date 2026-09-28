"""通过 X-User-ID 请求头选择用户并校验用户存在。"""

from typing import Annotated

from fastapi import Depends, Header

from app.dependencies import WebResourcesDep
from app.identity.errors import UserSelectionRequiredError
from app.identity.models.account import User
from app.identity.repositories.identity import IdentityPGRepo


async def _get_current_user(
    resources: WebResourcesDep,
    user_id: Annotated[int | None, Header(alias="X-User-ID", gt=0)] = None,
) -> User:
    """根据 X-User-ID 读取用户，缺少 ID 或用户不存在时抛出异常。"""
    if user_id is None:
        raise UserSelectionRequiredError
    async with resources.auth.session() as session:
        user = await IdentityPGRepo(session).get_user_by_id(user_id)
    if user is None:
        raise UserSelectionRequiredError(detail="所选用户不存在，请重新选择")
    return user


CurrentUserDep = Annotated[User, Depends(_get_current_user)]
