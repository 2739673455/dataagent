"""HTTP 入口读取当前应用资源；业务服务不依赖此模块。"""

from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.identity.application import IdentityService
from app.identity.contracts import AuthenticatedUser
from app.identity.errors import AuthenticationRequiredError
from app.runtime import WebResources


def get_web_resources(request: Request) -> WebResources:
    """只返回当前应用 lifespan 已初始化的资源。"""
    return cast(WebResources, request.app.state.resources)


WebResourcesDep = Annotated[WebResources, Depends(get_web_resources)]

_bearer = HTTPBearer(auto_error=False)


async def _get_current_user(
    resources: WebResourcesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    """HTTP 层解析 Bearer Header，身份认证由 Identity 公开用例完成。"""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise AuthenticationRequiredError
    return await resources.identity.authenticate(credentials.credentials)


CurrentUserDep = Annotated[AuthenticatedUser, Depends(_get_current_user)]


async def _require_admin(current_user: CurrentUserDep) -> AuthenticatedUser:
    """将 HTTP 管理员限制绑定到公开身份能力。"""
    IdentityService.require_admin(current_user)
    return current_user


AdminUserDep = Annotated[AuthenticatedUser, Depends(_require_admin)]


async def _require_analysis_access(
    current_user: CurrentUserDep, resources: WebResourcesDep
) -> AuthenticatedUser:
    """在独立认证会话中检查分析访问条件。"""
    await resources.identity.require_analysis_access(current_user)
    return current_user


AnalysisUserDep = Annotated[AuthenticatedUser, Depends(_require_analysis_access)]
