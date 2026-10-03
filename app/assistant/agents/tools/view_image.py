"""Agent 工作区图片查看工具及其请求契约。"""

from typing import Literal

from langchain.tools import tool
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import Field

from app.assistant.contracts import NonEmptyText, StrictProtocolModel
from app.sandbox import resolve_sandbox_path
from app.sandbox.errors import SandboxPathError

IMAGE_VIEW_TOOL_NAME = "view_image"
_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


class ImageViewInput(StrictProtocolModel):
    """请求附件 Middleware 在下一次模型调用前临时加载一张图片。"""

    f_path: NonEmptyText = Field(
        description="图片路径；相对路径从当前 Session 工作目录解析，绝对路径直接使用。"
    )


class ImageViewRequest(ImageViewInput):
    """持久化的图片加载请求，文件访问时校验路径。"""

    type: Literal["image_view_request"] = "image_view_request"


def is_supported_image_path(path: str) -> bool:
    """根据扩展名判断工作区路径是否为支持的图片。"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return suffix in _IMAGE_SUFFIXES


def supports_view_image_tool(model: BaseChatModel) -> bool:
    """判断模型传输层是否支持图片工具结果。"""
    return bool(model.profile and model.profile.get("image_tool_message"))


def create_view_image_tool(working_directory: str) -> BaseTool:
    """创建图片查看请求工具。

    工具结果只持久化图片路径。MessageContextMiddleware 会在下一次
    模型调用前读取该请求，把图片内容临时投影到 ToolMessage 副本中，避免
    base64 图片进入 LangGraph Checkpoint。
    """

    @tool(IMAGE_VIEW_TOOL_NAME, args_schema=ImageViewInput)
    def view_image(f_path: str) -> dict[str, object]:
        """请求加载沙箱内的图片。"""
        try:
            path = resolve_sandbox_path(f_path, working_directory)
        except SandboxPathError:
            return {"status": "error", "code": "invalid_path", "path": f_path}
        request = ImageViewRequest.model_construct(f_path=path)
        if not is_supported_image_path(request.f_path):
            return {
                "status": "error",
                "code": "unsupported_image_type",
                "path": request.f_path,
            }
        return request.model_dump(mode="json")

    return view_image
