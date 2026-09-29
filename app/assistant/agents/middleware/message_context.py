"""将用户消息接收时间和工具图片投影到模型请求。"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage

from app.assistant.agents.tools.view_image import (
    IMAGE_VIEW_TOOL_NAME,
    supports_view_image_tool,
)


def _collect_image_paths(request: ModelRequest[Any]) -> dict[int, str]:
    """按消息位置记录图片路径，每条工具消息只解析一次。"""
    if not supports_view_image_tool(request.model):
        return {}
    paths = {}
    for index, message in enumerate(request.messages):
        if (
            isinstance(message, ToolMessage)
            and message.name == IMAGE_VIEW_TOOL_NAME
            and isinstance(message.content, str)
        ):
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(payload, dict)
                and payload.get("type") == "image_view_request"
                and isinstance(path := payload.get("f_path"), str)
                and path.strip()
            ):
                paths[index] = path
    return paths


def _image_content_block(path: str, content: bytes) -> dict[str, str]:
    """将图片字节编码为 LangChain 标准图片内容块。"""
    mime_type, _ = mimetypes.guess_type(path)
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "image",
        "base64": encoded,
        "mime_type": mime_type or "application/octet-stream",
    }


def _project_messages(
    messages: list[AnyMessage],
    image_paths: dict[int, str],
    responses: Sequence[FileDownloadResponse],
) -> list[AnyMessage]:
    """投影已记录的用户消息时间及图片，不改写持久化消息。"""
    downloaded = {response.path: response for response in responses}
    projected: list[AnyMessage] = []
    for index, message in enumerate(messages):
        received_at = message.additional_kwargs.get("received_at")
        if isinstance(message, HumanMessage) and isinstance(received_at, str):
            content = (
                [{"type": "text", "text": message.content}]
                if isinstance(message.content, str)
                else list(message.content)
            )
            timestamp = {
                "type": "text",
                "text": f"用户消息接收时间（含时区）：{received_at}",
            }
            projected.append(
                message.model_copy(update={"content": [timestamp, *content]})
            )
            continue
        if (path := image_paths.get(index)) is not None:
            response = downloaded.get(path)
            view_content: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": f"图片路径：`{path}`",
                }
            ]
            if response is not None and response.content is not None:
                view_content.append(_image_content_block(path, response.content))
            else:
                payload = json.dumps(
                    {
                        "status": "error",
                        "path": path,
                        "error": str(response.error)
                        if response is not None
                        else "unavailable",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                view_content.append({"type": "text", "text": payload})
            projected.append(
                message.model_copy(update={"content": cast(Any, view_content)})
            )
            continue
        projected.append(message)
    return projected


class MessageContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """向模型提供用户消息时间和 view_image 图片，不影响前端展示。"""

    def __init__(
        self,
        backend: BackendProtocol,
    ) -> None:
        """绑定当前 Agent 的文件后端。"""
        self._backend = backend

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步读取当前需要查看的图片并投影模型请求。"""
        image_paths = _collect_image_paths(request)
        paths = list(dict.fromkeys(image_paths.values()))
        responses = self._backend.download_files(paths) if paths else []
        messages = _project_messages(request.messages, image_paths, responses)
        return handler(request.override(messages=messages))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步读取当前需要查看的图片并投影模型请求。"""
        image_paths = _collect_image_paths(request)
        paths = list(dict.fromkeys(image_paths.values()))
        responses = await self._backend.adownload_files(paths) if paths else []
        messages = _project_messages(request.messages, image_paths, responses)
        return await handler(request.override(messages=messages))
