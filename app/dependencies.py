"""HTTP 入口读取当前应用资源；业务服务不依赖此模块。"""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.runtime import WebResources


def get_web_resources(request: Request) -> WebResources:
    """只返回当前应用 lifespan 已初始化的资源。"""
    return cast(WebResources, request.app.state.resources)


WebResourcesDep = Annotated[WebResources, Depends(get_web_resources)]
