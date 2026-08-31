"""所有 Agent 共用的用户消息附件模型投影。"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, cast

from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, ToolMessage
from loguru import logger
from pydantic import Field, ValidationError

from app.assistant.agents.contracts import NonEmptyText, StrictProtocolModel
from app.assistant.agents.middleware.user_message_metadata import (
    USER_MESSAGE_METADATA_KEY,
)

USER_MESSAGE_ATTACHMENTS_KEY = "dataagent_user_message_attachments"
IMAGE_VIEW_TOOL_NAME = "view_image"

_ATTACHMENTS_TAG = "user_message_attachments"
_ATTACHMENT_ERROR_TAG = "attachment_error"
_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


class UserMessageAttachment(StrictProtocolModel):
    """一项用户消息附件引用"""

    f_path: NonEmptyText


class UserMessageAttachments(StrictProtocolModel):
    """一条用户消息持久化的附件引用"""

    attachments: list[UserMessageAttachment] = Field(default_factory=list)


class ImageViewRequest(StrictProtocolModel):
    """请求 Middleware 在下一次模型调用前临时加载一张图片"""

    type: Literal["image_view_request"] = "image_view_request"
    f_path: NonEmptyText


def is_image_path(path: str) -> bool:
    """根据扩展名判断工作区路径是否为支持的图片"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return suffix in _IMAGE_SUFFIXES


def _model_workspace_path(path: str) -> str:
    """把持久化的相对附件路径投影为所有 Agent 都能读取的会话绝对路径"""
    return path if path.startswith("/") else f"/{path}"


def _read_attachments(message: HumanMessage) -> UserMessageAttachments | None:
    """读取并校验用户消息中持久化的附件引用。"""
    payload = message.additional_kwargs.get(USER_MESSAGE_ATTACHMENTS_KEY)
    if payload is None:
        return None
    try:
        return UserMessageAttachments.model_validate(payload)
    except ValidationError:
        logger.warning(f"用户消息附件元数据无效: message_id={message.id}")
        return None


def _read_image_view_request(message: ToolMessage) -> ImageViewRequest | None:
    """读取 view_image 工具持久化的图片加载请求"""
    if message.name != IMAGE_VIEW_TOOL_NAME or not isinstance(message.content, str):
        return None
    try:
        payload = json.loads(message.content)
        if not isinstance(payload, dict):
            return None
        return ImageViewRequest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        return None


def _attachment_context_block(
    attachments: UserMessageAttachments,
) -> dict[str, str]:
    """生成向模型说明附件路径和查看工具的上下文块。"""
    files: list[dict[str, str]] = []
    images: list[dict[str, str]] = []
    for attachment in attachments.attachments:
        item = {"path": _model_workspace_path(attachment.f_path)}
        if is_image_path(attachment.f_path):
            item["tool"] = IMAGE_VIEW_TOOL_NAME
            images.append(item)
        else:
            item["tool"] = "read_file"
            files.append(item)
    payload = json.dumps(
        {"files": files, "images": images},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "text",
        "text": f"<{_ATTACHMENTS_TAG}>{payload}</{_ATTACHMENTS_TAG}>",
    }


def _attachment_error_block(path: str, error: str) -> dict[str, str]:
    """生成图片附件读取失败时的模型上下文块。"""
    payload = json.dumps(
        {"path": path, "error": error},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "text",
        "text": f"<{_ATTACHMENT_ERROR_TAG}>{payload}</{_ATTACHMENT_ERROR_TAG}>",
    }


def _image_content_block(path: str, content: bytes) -> dict[str, str]:
    """将图片字节编码为模型多模态消息内容块。"""
    mime_type, _ = mimetypes.guess_type(path)
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "image_url",
        "image_url": f"data:{mime_type or 'application/octet-stream'};base64,{encoded}",
    }


def _content_list(message: BaseMessage) -> list[str | dict[str, Any]] | None:
    """将支持的消息内容复制并规范化为可追加的内容块列表。"""
    if isinstance(message.content, str):
        return [{"type": "text", "text": message.content}]
    if isinstance(message.content, list):
        return cast("list[str | dict[str, Any]]", list(message.content))
    return None


def _downloaded_content(
    responses: Sequence[FileDownloadResponse],
) -> dict[str, FileDownloadResponse]:
    """按工作区路径索引文件下载结果。"""
    return {response.path: response for response in responses}


def _download_paths(messages: list[AnyMessage]) -> list[str]:
    """收集本次模型调用需要临时加载的去重图片路径。"""
    latest_user_index = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
            and USER_MESSAGE_METADATA_KEY in message.additional_kwargs
        ),
        default=-1,
    )
    paths: list[str] = []
    if latest_user_index >= 0:
        latest = cast(HumanMessage, messages[latest_user_index])
        metadata = _read_attachments(latest)
        if metadata is not None:
            paths.extend(
                item.f_path
                for item in metadata.attachments
                if is_image_path(item.f_path)
            )
    paths.extend(
        image_request.f_path
        for message in messages[latest_user_index + 1 :]
        if isinstance(message, ToolMessage)
        and (image_request := _read_image_view_request(message)) is not None
    )
    return list(dict.fromkeys(paths))


def _project_messages(
    messages: list[AnyMessage],
    responses: Sequence[FileDownloadResponse],
) -> list[AnyMessage]:
    """将附件说明和已加载图片投影到本次模型消息副本。"""
    downloaded = _downloaded_content(responses)
    latest_user_index = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
            and USER_MESSAGE_METADATA_KEY in message.additional_kwargs
        ),
        default=-1,
    )
    projected: list[AnyMessage] = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            metadata = _read_attachments(message)
            content = _content_list(message)
            if metadata is None or content is None:
                projected.append(message)
                continue
            content.append(_attachment_context_block(metadata))
            if index == latest_user_index:
                for attachment in metadata.attachments:
                    if not is_image_path(attachment.f_path):
                        continue
                    response = downloaded.get(attachment.f_path)
                    if response is not None and response.content is not None:
                        content.append(
                            _image_content_block(attachment.f_path, response.content)
                        )
                    else:
                        content.append(
                            _attachment_error_block(
                                attachment.f_path,
                                str(response.error)
                                if response is not None
                                else "unavailable",
                            )
                        )
            projected.append(message.model_copy(update={"content": cast(Any, content)}))
            continue

        if isinstance(message, ToolMessage) and index > latest_user_index:
            image_request = _read_image_view_request(message)
            if image_request is not None:
                response = downloaded.get(image_request.f_path)
                view_content: list[dict[str, str]] = [
                    {
                        "type": "text",
                        "text": f"图片路径：`{image_request.f_path}`",
                    }
                ]
                if response is not None and response.content is not None:
                    view_content.append(
                        _image_content_block(image_request.f_path, response.content)
                    )
                else:
                    view_content.append(
                        _attachment_error_block(
                            image_request.f_path,
                            str(response.error)
                            if response is not None
                            else "unavailable",
                        )
                    )
                projected.append(
                    message.model_copy(update={"content": cast(Any, view_content)})
                )
                continue
        projected.append(message)
    return projected


class UserMessageAttachmentMiddleware(AgentMiddleware[Any, Any, Any]):
    """临时展开用户附件，并消费 view_image 产生的图片加载请求"""

    def __init__(self, backend: BackendProtocol) -> None:
        """绑定用于读取当前 Agent 工作区文件的后端。"""
        self._backend = backend

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步读取当前需要查看的图片并投影模型请求"""
        paths = _download_paths(request.messages)
        responses = self._backend.download_files(paths) if paths else []
        messages = _project_messages(request.messages, responses)
        return handler(request.override(messages=messages))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步读取当前需要查看的图片并投影模型请求"""
        paths = _download_paths(request.messages)
        responses = await self._backend.adownload_files(paths) if paths else []
        messages = _project_messages(request.messages, responses)
        return await handler(request.override(messages=messages))
