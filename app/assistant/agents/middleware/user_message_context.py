"""所有 Agent 共用的用户消息私有上下文与模型输入投影。"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from loguru import logger
from pydantic import Field, ValidationError, field_validator

from app.assistant.agents.tools.view_image import (
    IMAGE_VIEW_TOOL_NAME,
    ImageViewRequest,
    is_supported_image_path,
    supports_view_image_tool,
)
from app.assistant.execution.types import NonEmptyText, StrictProtocolModel
from app.sandbox.paths import normalize_attachment_path, resolve_sandbox_path

USER_MESSAGE_CONTEXT_KEY = "dataagent_user_message_context"
_MESSAGE_CONTEXT_TAG = "user_message_context"
_ATTACHMENTS_TAG = "user_message_attachments"
_ATTACHMENT_ERROR_TAG = "attachment_error"


class UserMessageAttachment(StrictProtocolModel):
    """一项用户消息附件引用。"""

    f_path: NonEmptyText

    @field_validator("f_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """只持久化规范的 Conversation 内相对路径。"""
        return normalize_attachment_path(value)


class UserMessageContext(StrictProtocolModel):
    """LangChain content 无法承载的用户消息私有上下文。"""

    received_at: datetime
    attachments: list[UserMessageAttachment] = Field(default_factory=list)

    @field_validator("received_at", mode="before")
    @classmethod
    def parse_received_at(cls, value: object) -> object:
        """解析 Checkpoint 中保存的 ISO 8601 时间。"""
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: datetime) -> datetime:
        """要求时区信息并统一为 UTC。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at 必须包含时区")
        return value.astimezone(UTC)


def read_user_message_context(message: HumanMessage) -> UserMessageContext | None:
    """读取并校验一条真实用户消息的私有上下文。"""
    payload = message.additional_kwargs.get(USER_MESSAGE_CONTEXT_KEY)
    if payload is None:
        return None
    try:
        return UserMessageContext.model_validate(payload)
    except ValidationError:
        logger.warning(f"用户消息私有上下文无效: message_id={message.id}")
        return None


def _read_image_view_request(message: ToolMessage) -> ImageViewRequest | None:
    """读取 view_image 工具持久化的图片加载请求。"""
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
    attachments: UserMessageContext,
    *,
    conversation_dir: str,
    image_inputs_enabled: bool,
) -> dict[str, str]:
    """生成向模型说明附件路径和图片能力的上下文块。"""
    files: list[dict[str, str]] = []
    images: list[dict[str, str]] = []
    for attachment in attachments.attachments:
        item = {"path": resolve_sandbox_path(attachment.f_path, conversation_dir)}
        if is_supported_image_path(attachment.f_path):
            images.append(item)
        else:
            item["tool"] = "read_file"
            files.append(item)
    context: dict[str, Any] = {"files": files, "images": images}
    if images and not image_inputs_enabled:
        context["image_notice"] = (
            "当前模型的图片识别功能未开启，图片不会被自动加载。"
            "请勿根据文件名推测图片内容。"
        )
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return {
        "type": "text",
        "text": f"<{_ATTACHMENTS_TAG}>{payload}</{_ATTACHMENTS_TAG}>",
    }


def _image_content_block(path: str, content: bytes) -> dict[str, str]:
    """将图片字节编码为 LangChain 标准图片内容块。"""
    mime_type, _ = mimetypes.guess_type(path)
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "image",
        "base64": encoded,
        "mime_type": mime_type or "application/octet-stream",
    }


def _download_paths(
    messages: list[AnyMessage],
    *,
    conversation_dir: str,
    load_user_images: bool,
    load_tool_images: bool,
) -> list[str]:
    """收集本次模型调用需要临时加载的去重图片路径。"""
    paths: list[str] = []
    if load_user_images:
        for message in messages:
            if not isinstance(message, HumanMessage):
                continue
            context = read_user_message_context(message)
            if context is not None:
                paths.extend(
                    resolve_sandbox_path(item.f_path, conversation_dir)
                    for item in context.attachments
                    if is_supported_image_path(item.f_path)
                )
    if load_tool_images:
        paths.extend(
            image_request.f_path
            for message in messages
            if isinstance(message, ToolMessage)
            and (image_request := _read_image_view_request(message)) is not None
        )
    return list(dict.fromkeys(paths))


def _project_human_message(
    message: HumanMessage,
    downloaded: dict[str, FileDownloadResponse],
    *,
    conversation_dir: str,
    project_user_images: bool,
) -> HumanMessage:
    """一次性投影接收时间和附件上下文。"""
    context = read_user_message_context(message)
    if context is None:
        return message
    if isinstance(message.content, str):
        content: list[str | dict[str, Any]] = [
            {"type": "text", "text": message.content}
        ]
    elif isinstance(message.content, list):
        content = list(message.content)
    else:
        logger.warning(f"用户消息内容类型无效: message_id={message.id}")
        return message

    payload = json.dumps(
        {"received_at": context.received_at.isoformat()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content.insert(
        0,
        {
            "type": "text",
            "text": f"<{_MESSAGE_CONTEXT_TAG}>{payload}</{_MESSAGE_CONTEXT_TAG}>",
        },
    )
    if context.attachments:
        content.append(
            _attachment_context_block(
                context,
                conversation_dir=conversation_dir,
                image_inputs_enabled=project_user_images,
            )
        )
        if project_user_images:
            for attachment in context.attachments:
                if not is_supported_image_path(attachment.f_path):
                    continue
                model_path = resolve_sandbox_path(
                    attachment.f_path,
                    conversation_dir,
                )
                response = downloaded.get(model_path)
                if response is not None and response.content is not None:
                    content.append(_image_content_block(model_path, response.content))
                else:
                    payload = json.dumps(
                        {
                            "path": model_path,
                            "error": str(response.error)
                            if response is not None
                            else "unavailable",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    content.append(
                        {
                            "type": "text",
                            "text": f"<{_ATTACHMENT_ERROR_TAG}>{payload}</{_ATTACHMENT_ERROR_TAG}>",
                        }
                    )
    return message.model_copy(update={"content": cast(Any, content)})


def _project_messages(
    messages: list[AnyMessage],
    responses: Sequence[FileDownloadResponse],
    *,
    conversation_dir: str,
    project_user_images: bool,
    project_tool_images: bool,
) -> list[AnyMessage]:
    """将私有消息上下文和已加载图片投影到本次模型请求。"""
    downloaded = {response.path: response for response in responses}
    projected: list[AnyMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            projected.append(
                _project_human_message(
                    message,
                    downloaded,
                    conversation_dir=conversation_dir,
                    project_user_images=project_user_images,
                )
            )
            continue
        if project_tool_images and isinstance(message, ToolMessage):
            image_request = _read_image_view_request(message)
            if image_request is not None:
                response = downloaded.get(image_request.f_path)
                view_content: list[dict[str, Any]] = [
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
                    payload = json.dumps(
                        {
                            "status": "error",
                            "path": image_request.f_path,
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


def _image_projection_options(request: ModelRequest[Any]) -> tuple[bool, bool]:
    """计算用户消息图片和工具图片的投影策略。"""
    profile = request.model.profile
    return (
        bool(profile and profile.get("image_inputs")),
        supports_view_image_tool(request.model),
    )


class UserMessageContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """持久化并投影用户接收时间、附件和图片。"""

    def __init__(
        self,
        backend: BackendProtocol,
        conversation_dir: str,
    ) -> None:
        """绑定当前 Agent 的文件后端和会话目录。"""
        self._backend = backend
        self._conversation_dir = conversation_dir

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步读取当前需要查看的图片并投影模型请求。"""
        user_images, tool_images = _image_projection_options(request)
        paths = _download_paths(
            request.messages,
            conversation_dir=self._conversation_dir,
            load_user_images=user_images,
            load_tool_images=tool_images,
        )
        responses = self._backend.download_files(paths) if paths else []
        messages = _project_messages(
            request.messages,
            responses,
            conversation_dir=self._conversation_dir,
            project_user_images=user_images,
            project_tool_images=tool_images,
        )
        if all(
            projected is original
            for projected, original in zip(messages, request.messages, strict=True)
        ):
            return handler(request)
        return handler(request.override(messages=messages))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步读取当前需要查看的图片并投影模型请求。"""
        user_images, tool_images = _image_projection_options(request)
        paths = _download_paths(
            request.messages,
            conversation_dir=self._conversation_dir,
            load_user_images=user_images,
            load_tool_images=tool_images,
        )
        responses = await self._backend.adownload_files(paths) if paths else []
        messages = _project_messages(
            request.messages,
            responses,
            conversation_dir=self._conversation_dir,
            project_user_images=user_images,
            project_tool_images=tool_images,
        )
        if all(
            projected is original
            for projected, original in zip(messages, request.messages, strict=True)
        ):
            return await handler(request)
        return await handler(request.override(messages=messages))
