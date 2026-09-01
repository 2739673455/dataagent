"""所有 Agent 共用的用户消息附件模型投影。"""

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
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, ToolMessage
from loguru import logger
from pydantic import Field, ValidationError, field_validator

from app.assistant.agents.contracts import NonEmptyText, StrictProtocolModel
from app.assistant.agents.tools.view_image import (
    IMAGE_VIEW_TOOL_NAME,
    ImageViewRequest,
    is_supported_image_path,
    supports_view_image_tool,
)
from app.sandbox.paths import normalize_attachment_path, resolve_sandbox_path

USER_MESSAGE_ATTACHMENTS_KEY = "dataagent_user_message_attachments"

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


class UserMessageAttachments(StrictProtocolModel):
    """一条用户消息持久化的附件引用。"""

    attachments: list[UserMessageAttachment] = Field(default_factory=list)


def _model_workspace_path(path: str, conversation_dir: str) -> str:
    """把持久化的相对附件路径投影为容器绝对路径。"""
    return resolve_sandbox_path(path, conversation_dir)


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
    attachments: UserMessageAttachments,
    *,
    conversation_dir: str,
    image_inputs_enabled: bool,
) -> dict[str, str]:
    """生成向模型说明附件路径和图片能力的上下文块。"""
    files: list[dict[str, str]] = []
    images: list[dict[str, str]] = []
    for attachment in attachments.attachments:
        item = {"path": _model_workspace_path(attachment.f_path, conversation_dir)}
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
    payload = json.dumps(
        context,
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
    """将图片字节编码为 LangChain 标准图片内容块。"""
    mime_type, _ = mimetypes.guess_type(path)
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "image",
        "base64": encoded,
        "mime_type": mime_type or "application/octet-stream",
    }


def _image_view_error_block(path: str, error: str) -> dict[str, str]:
    """生成 view_image 工具读取失败时的文本结果。"""
    payload = json.dumps(
        {"status": "error", "path": path, "error": error},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {"type": "text", "text": payload}


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


def _latest_user_index(messages: list[AnyMessage]) -> int:
    """定位当前模型上下文中的最后一条用户消息。"""
    return max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )


def _download_paths(
    messages: list[AnyMessage],
    *,
    conversation_dir: str,
    load_user_images: bool,
    load_tool_images: bool,
) -> list[str]:
    """收集本次模型调用需要临时加载的去重图片路径。"""
    latest_user_index = _latest_user_index(messages)
    paths: list[str] = []
    if load_user_images:
        for message in messages:
            if not isinstance(message, HumanMessage):
                continue
            metadata = _read_attachments(message)
            if metadata is not None:
                paths.extend(
                    _model_workspace_path(item.f_path, conversation_dir)
                    for item in metadata.attachments
                    if is_supported_image_path(item.f_path)
                )
    if load_tool_images:
        # view_image 的图片只属于最新用户回合后的工具续轮。重新展开更早回合的
        # 请求会让已经完成的图片在之后每次模型调用中重复进入上下文。
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
    *,
    conversation_dir: str,
    project_user_images: bool,
    project_tool_images: bool,
) -> list[AnyMessage]:
    """将附件说明和已加载图片投影到本次模型消息副本。"""
    downloaded = _downloaded_content(responses)
    latest_user_index = _latest_user_index(messages)
    projected: list[AnyMessage] = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            metadata = _read_attachments(message)
            content = _content_list(message)
            if metadata is None or content is None:
                projected.append(message)
                continue
            content.append(
                _attachment_context_block(
                    metadata,
                    conversation_dir=conversation_dir,
                    image_inputs_enabled=project_user_images,
                )
            )
            if project_user_images:
                for attachment in metadata.attachments:
                    if not is_supported_image_path(attachment.f_path):
                        continue
                    model_path = _model_workspace_path(
                        attachment.f_path,
                        conversation_dir,
                    )
                    response = downloaded.get(model_path)
                    if response is not None and response.content is not None:
                        content.append(
                            _image_content_block(model_path, response.content)
                        )
                    else:
                        content.append(
                            _attachment_error_block(
                                model_path,
                                str(response.error)
                                if response is not None
                                else "unavailable",
                            )
                        )
            projected.append(message.model_copy(update={"content": cast(Any, content)}))
            continue

        if (
            project_tool_images
            and isinstance(message, ToolMessage)
            and index > latest_user_index
        ):
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
                    view_content.append(
                        _image_view_error_block(
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


def _image_projection_options(request: ModelRequest[Any]) -> tuple[bool, bool]:
    """计算用户消息图片和工具图片的投影策略。"""
    profile = request.model.profile
    return (
        bool(profile and profile.get("image_inputs")),
        supports_view_image_tool(request.model),
    )


class UserMessageAttachmentMiddleware(AgentMiddleware[Any, Any, Any]):
    """临时展开用户附件，并消费 view_image 产生的图片加载请求。"""

    def __init__(self, backend: BackendProtocol, conversation_dir: str) -> None:
        """绑定用于读取当前 Agent 工作区文件的后端。"""
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
        return await handler(request.override(messages=messages))
