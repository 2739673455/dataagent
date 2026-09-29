"""Agent 工作区图片查看工具。"""

from typing import Annotated

from langchain.tools import tool
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import StringConstraints

from app.sandbox.errors import SandboxPathError
from app.sandbox.paths import normalize_sandbox_path

IMAGE_VIEW_TOOL_NAME = "view_image"
_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


def is_supported_image_path(path: str) -> bool:
    """根据扩展名判断工作区路径是否为支持的图片。"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return suffix in _IMAGE_SUFFIXES


def supports_view_image_tool(model: BaseChatModel) -> bool:
    """判断模型传输层是否支持图片工具结果。"""
    return bool(model.profile and model.profile.get("image_tool_message"))


def create_view_image_tools(model: BaseChatModel) -> tuple[BaseTool, ...]:
    """创建图片查看请求工具。

    工具结果只持久化图片路径。MessageContextMiddleware 会在下一次
    模型调用前读取该请求，把图片内容临时投影到 ToolMessage 副本中，避免
    base64 图片进入 LangGraph Checkpoint。
    """

    if not supports_view_image_tool(model):
        return ()

    @tool(IMAGE_VIEW_TOOL_NAME)
    def view_image(
        f_path: Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1),
            "图片路径；相对路径从当前会话工作目录解析，绝对路径直接使用。",
        ],
    ) -> dict[str, object]:
        """请求加载沙箱内的图片。"""
        try:
            normalized_path = normalize_sandbox_path(f_path)
        except SandboxPathError:
            return {
                "status": "error",
                "message": "图片路径无效，请使用相对当前会话目录的路径或完整绝对路径",
                "path": f_path,
            }
        if not is_supported_image_path(normalized_path):
            return {
                "status": "error",
                "message": "不支持的图片类型，请使用 PNG、JPEG、GIF、WebP 或 BMP 图片",
                "path": normalized_path,
            }
        return {"type": "image_view_request", "f_path": normalized_path}

    return (view_image,)
