"""Agent 历史图片查看请求工具。"""

from typing import Annotated

from langchain.tools import tool
from langchain_core.tools import BaseTool

from app.assistant.agents.contracts import NonEmptyText
from app.assistant.agents.middleware.user_message_attachments import (
    ImageViewRequest,
    is_image_path,
)


def create_image_view_request_tool() -> BaseTool:
    """创建图片查看请求工具。

    工具结果只持久化图片路径。UserMessageAttachmentMiddleware 会在下一次
    模型调用前读取该请求，把图片内容临时投影到 ToolMessage 副本中，避免
    base64 图片进入 LangGraph Checkpoint。
    """

    @tool("view_image")
    def view_image(
        f_path: Annotated[
            NonEmptyText,
            "用户消息附件中列出的工作区图片路径",
        ],
    ) -> dict[str, object]:
        """请求加载历史用户消息附带的工作区图片。"""
        if not is_image_path(f_path):
            return {
                "status": "error",
                "code": "unsupported_image_type",
                "path": f_path,
            }
        return ImageViewRequest(f_path=f_path).model_dump(mode="json")

    return view_image
