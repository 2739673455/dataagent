"""Agent 共用的消息上下文、模型输入投影与响应时间戳。"""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from deepagents.backends.protocol import BackendProtocol, FileDownloadResponse
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from loguru import logger
from pydantic import Field, ValidationError, field_validator

from app.assistant.agents.tools.view_image import (
    IMAGE_VIEW_TOOL_NAME,
    ImageViewRequest,
    is_supported_image_path,
    supports_view_image_tool,
)
from app.assistant.contracts import (
    MESSAGE_CREATED_AT_KEY,
    NonEmptyText,
    StrictProtocolModel,
)
from app.assistant.services.shell_jobs import ShellJobRuntime
from app.sandbox.application import resolve_sandbox_path
from app.sandbox.errors import SandboxPathError

USER_MESSAGE_CONTEXT_KEY = "dataagent_user_message_context"
SHELL_JOB_CONTEXT_KEY = "dataagent_shell_jobs"
_MESSAGE_CONTEXT_TAG = "user_message_context"
_ATTACHMENTS_TAG = "user_message_attachments"
_ATTACHMENT_ERROR_TAG = "attachment_error"
_SHELL_JOB_CONTEXT_TAG = "shell_jobs"


class UserMessageAttachment(StrictProtocolModel):
    """一项用户消息附件引用。"""

    f_path: NonEmptyText


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


class ShellJobReference(TypedDict):
    """一项可由 Shell Job 工具继续查询的稳定引用。"""

    job_id: str
    output_path: str


class ShellJobMessageContext(TypedDict):
    """持久化在真实用户消息中的 Shell Job 快照。"""

    jobs: list[ShellJobReference]


class MessageContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """准备用户消息上下文，并在模型响应进入状态前补充创建时间。"""

    def __init__(
        self,
        backend: BackendProtocol,
        conversation_dir: str,
        shell_jobs: ShellJobRuntime,
        *,
        working_directory: str,
    ) -> None:
        """绑定当前 Agent 的文件后端和 Shell Job Runtime。"""
        self._backend = backend
        self._conversation_dir = conversation_dir
        self._working_directory = working_directory
        self._shell_jobs = shell_jobs

    def before_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """在模型调用前把首次出现的后台任务引用冻结到当前用户回合。"""
        del runtime
        jobs = self._shell_jobs.list()
        if not jobs:
            return None
        for message in reversed(state["messages"]):
            if not isinstance(message, HumanMessage):
                continue
            if SHELL_JOB_CONTEXT_KEY in message.additional_kwargs:
                return None
            additional_kwargs = {
                **message.additional_kwargs,
                SHELL_JOB_CONTEXT_KEY: {
                    "jobs": [
                        {"job_id": job.job_id, "output_path": job.output_path}
                        for job in jobs
                    ]
                },
            }
            return {
                "messages": [
                    message.model_copy(update={"additional_kwargs": additional_kwargs})
                ]
            }
        return None

    async def abefore_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """异步模型调用沿用相同的 Shell Job 快照规则。"""
        return self.before_model(state, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步读取当前需要查看的图片并投影模型请求。"""
        user_images, tool_images = _image_projection_options(request)
        attachment_paths, image_requests = _file_references(
            request.messages, self._conversation_dir, self._working_directory
        )
        paths = _download_paths(
            attachment_paths,
            image_requests,
            load_user_images=user_images,
            load_tool_images=tool_images,
        )
        responses = self._backend.download_files(paths) if paths else []
        messages = _project_messages(
            request.messages,
            responses,
            attachment_paths=attachment_paths,
            image_requests=image_requests,
            project_user_images=user_images,
            project_tool_images=tool_images,
        )
        if not all(
            projected is original
            for projected, original in zip(messages, request.messages, strict=True)
        ):
            request = request.override(messages=messages)
        return _stamp_response(handler(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步读取当前需要查看的图片并投影模型请求。"""
        user_images, tool_images = _image_projection_options(request)
        attachment_paths, image_requests = _file_references(
            request.messages, self._conversation_dir, self._working_directory
        )
        paths = _download_paths(
            attachment_paths,
            image_requests,
            load_user_images=user_images,
            load_tool_images=tool_images,
        )
        responses = await self._backend.adownload_files(paths) if paths else []
        messages = _project_messages(
            request.messages,
            responses,
            attachment_paths=attachment_paths,
            image_requests=image_requests,
            project_user_images=user_images,
            project_tool_images=tool_images,
        )
        if not all(
            projected is original
            for projected, original in zip(messages, request.messages, strict=True)
        ):
            request = request.override(messages=messages)
        return _stamp_response(await handler(request))


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


def _context_content_block(context: UserMessageContext) -> dict[str, str]:
    """将接收时间编码为供模型读取的文本内容块。"""
    payload = json.dumps(
        {"received_at": context.received_at.isoformat()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "text",
        "text": f"<{_MESSAGE_CONTEXT_TAG}>{payload}</{_MESSAGE_CONTEXT_TAG}>",
    }


def _read_attachments(message: HumanMessage) -> UserMessageContext | None:
    """读取并校验用户消息中持久化的附件引用。"""
    context = read_user_message_context(message)
    return context if context is not None and context.attachments else None


def _read_image_view_request(message: ToolMessage) -> ImageViewRequest | None:
    """读取 view_image 工具持久化的图片加载请求。"""
    if message.name != IMAGE_VIEW_TOOL_NAME or not isinstance(message.content, str):
        return None
    try:
        return ImageViewRequest.model_validate_json(message.content)
    except ValidationError:
        return None


def _attachment_context_block(
    attachments: UserMessageContext,
    *,
    attachment_paths: dict[str, str],
    image_inputs_enabled: bool,
) -> dict[str, str]:
    """生成向模型说明附件路径和图片能力的上下文块。"""
    files: list[dict[str, str]] = []
    images: list[dict[str, str]] = []
    for attachment in attachments.attachments:
        path = attachment_paths.get(attachment.f_path)
        if path is None:
            continue
        item = {"path": path}
        if is_supported_image_path(attachment.f_path):
            images.append(item)
        else:
            item["tool"] = "read_file"
            files.append(item)
    context: dict[str, Any] = {"files": files, "images": images}
    if images and not image_inputs_enabled:
        context["image_notice"] = (
            "当前模型的图片识别功能未开启，图片不会被自动加载，请勿根据文件名推测图片内容。"
        )
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
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


def _shell_job_context_block(message: HumanMessage) -> dict[str, str] | None:
    """读取并编码一条用户消息持久化的 Shell Job 快照。"""
    payload = message.additional_kwargs.get(SHELL_JOB_CONTEXT_KEY)
    if payload is None:
        return None
    context = cast(ShellJobMessageContext, payload)
    return {
        "type": "text",
        "text": (
            f"<{_SHELL_JOB_CONTEXT_TAG}>"
            f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
            f"</{_SHELL_JOB_CONTEXT_TAG}>"
        ),
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


def _file_references(
    messages: list[AnyMessage],
    conversation_dir: str,
    working_directory: str,
) -> tuple[dict[str, str], dict[int, ImageViewRequest]]:
    """每次模型请求只解析一次附件路径与持久化图片请求。"""
    attachments: dict[str, str] = {}
    images: dict[int, ImageViewRequest] = {}
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            context = _read_attachments(message)
            if context is not None:
                for item in context.attachments:
                    if item.f_path not in attachments:
                        try:
                            attachments[item.f_path] = resolve_sandbox_path(
                                item.f_path,
                                conversation_dir,
                                allowed_root=conversation_dir,
                            )
                        except SandboxPathError:
                            continue
        elif isinstance(message, ToolMessage):
            request = _read_image_view_request(message)
            if request is not None:
                try:
                    path = resolve_sandbox_path(request.f_path, working_directory)
                except SandboxPathError:
                    continue
                images[index] = request.model_copy(update={"f_path": path})
    return attachments, images


def _download_paths(
    attachment_paths: dict[str, str],
    image_requests: dict[int, ImageViewRequest],
    *,
    load_user_images: bool,
    load_tool_images: bool,
) -> list[str]:
    """从已解析引用中收集需要加载的去重图片路径。"""
    paths = [
        path
        for reference, path in attachment_paths.items()
        if load_user_images and is_supported_image_path(reference)
    ]
    if load_tool_images:
        paths.extend(request.f_path for request in image_requests.values())
    return list(dict.fromkeys(paths))


def _project_human_message(
    message: HumanMessage,
    downloaded: dict[str, FileDownloadResponse],
    *,
    attachment_paths: dict[str, str],
    project_user_images: bool,
) -> HumanMessage:
    """一次性投影接收时间、附件和 Shell Job 上下文。"""
    context = read_user_message_context(message)
    shell_block = _shell_job_context_block(message)
    if context is None and shell_block is None:
        return message
    content = _content_list(message)
    if content is None:
        logger.warning(f"用户消息内容类型无效: message_id={message.id}")
        return message

    if context is not None:
        content.insert(0, _context_content_block(context))
        if context.attachments:
            content.append(
                _attachment_context_block(
                    context,
                    attachment_paths=attachment_paths,
                    image_inputs_enabled=project_user_images,
                )
            )
            for attachment in context.attachments:
                model_path = attachment_paths.get(attachment.f_path)
                if model_path is None:
                    content.append(
                        _attachment_error_block(attachment.f_path, "invalid_path")
                    )
                    continue
                if not project_user_images or not is_supported_image_path(model_path):
                    continue
                response = downloaded.get(model_path)
                if response is not None and response.content is not None:
                    content.append(_image_content_block(model_path, response.content))
                else:
                    content.append(
                        _attachment_error_block(
                            model_path,
                            str(response.error)
                            if response is not None
                            else "unavailable",
                        )
                    )
    if shell_block is not None:
        content.append(shell_block)
    return message.model_copy(update={"content": cast(Any, content)})


def _project_messages(
    messages: list[AnyMessage],
    responses: Sequence[FileDownloadResponse],
    *,
    attachment_paths: dict[str, str],
    project_user_images: bool,
    project_tool_images: bool,
    image_requests: dict[int, ImageViewRequest],
) -> list[AnyMessage]:
    """将私有消息上下文和已加载图片投影到本次模型请求。"""
    downloaded = {response.path: response for response in responses}
    projected: list[AnyMessage] = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            projected.append(
                _project_human_message(
                    message,
                    downloaded,
                    attachment_paths=attachment_paths,
                    project_user_images=project_user_images,
                )
            )
            continue
        if project_tool_images and isinstance(message, ToolMessage):
            image_request = image_requests.get(index)
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


def _stamp_response(response: ModelResponse[Any]) -> ModelResponse[Any]:
    """为模型响应中的消息补充统一创建时间。"""
    for message in response.result:
        message.additional_kwargs.setdefault(
            MESSAGE_CREATED_AT_KEY,
            datetime.now(UTC).isoformat(),
        )
    return response
