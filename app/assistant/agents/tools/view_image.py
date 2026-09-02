"""Agent 工作区图片查看工具及其请求契约。"""

from typing import Annotated, Literal

from langchain.tools import tool
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import field_validator

from app.assistant.agents.contracts import NonEmptyText, StrictProtocolModel
from app.sandbox.exceptions import SandboxPathError
from app.sandbox.paths import normalize_sandbox_path

IMAGE_VIEW_TOOL_NAME = "view_image"
_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


class ImageViewRequest(StrictProtocolModel):
    """请求附件 Middleware 在下一次模型调用前临时加载一张图片。"""

    type: Literal["image_view_request"] = "image_view_request"
    f_path: NonEmptyText

    @field_validator("f_path")
    @classmethod
    def validate_sandbox_path(cls, value: str) -> str:
        """按文件工具规则规范化相对路径或绝对路径。"""
        return normalize_sandbox_path(value)


def is_supported_image_path(path: str) -> bool:
    """根据扩展名判断工作区路径是否为支持的图片。"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return suffix in _IMAGE_SUFFIXES


def supports_view_image_tool(model: BaseChatModel) -> bool:
    """判断模型传输层是否支持图片工具结果。"""
    return bool(model.profile and model.profile.get("image_tool_message"))


def _create_view_image_tool() -> BaseTool:
    """创建图片查看请求工具。

    工具结果只持久化图片路径。UserMessageContextMiddleware 会在下一次
    模型调用前读取该请求，把图片内容临时投影到 ToolMessage 副本中，避免
    base64 图片进入 LangGraph Checkpoint。
    """

    @tool(IMAGE_VIEW_TOOL_NAME)
    def view_image(
        f_path: Annotated[
            NonEmptyText,
            "图片路径；相对路径从当前 Session 工作目录解析，绝对路径直接使用。",
        ],
    ) -> dict[str, object]:
        """请求加载沙箱内的图片。"""
        try:
            normalized_path = normalize_sandbox_path(f_path)
        except SandboxPathError:
            return {
                "status": "error",
                "code": "invalid_path",
                "path": f_path,
            }
        if not is_supported_image_path(normalized_path):
            return {
                "status": "error",
                "code": "unsupported_image_type",
                "path": normalized_path,
            }
        return ImageViewRequest(f_path=normalized_path).model_dump(mode="json")

    return view_image


def create_view_image_tools(model: BaseChatModel) -> tuple[BaseTool, ...]:
    """为支持图片工具结果的模型提供工作区图片查看工具。"""
    if not supports_view_image_tool(model):
        return ()
    return (_create_view_image_tool(),)
