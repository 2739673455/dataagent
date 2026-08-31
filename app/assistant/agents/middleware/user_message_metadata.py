"""用户消息私有元数据的模型请求投影。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage
from loguru import logger
from pydantic import ValidationError, field_validator

from app.assistant.agents.contracts import StrictProtocolModel

USER_MESSAGE_METADATA_KEY = "dataagent_user_message_metadata"
_MESSAGE_METADATA_TAG = "message_metadata"


class UserMessageMetadata(StrictProtocolModel):
    """仅供模型使用的用户消息元数据。"""

    received_at: datetime

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


def _read_user_message_metadata(
    message: HumanMessage,
) -> UserMessageMetadata | None:
    """读取并校验一条真实用户消息的私有元数据。"""
    if USER_MESSAGE_METADATA_KEY not in message.additional_kwargs:
        return None
    payload = message.additional_kwargs[USER_MESSAGE_METADATA_KEY]
    try:
        return UserMessageMetadata.model_validate(payload)
    except ValidationError:
        logger.warning(f"用户消息私有元数据无效: message_id={message.id}")
        return None


def _metadata_content_block(metadata: UserMessageMetadata) -> dict[str, str]:
    """将私有元数据编码为供模型读取的文本内容块。"""
    payload = json.dumps(
        metadata.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "text",
        "text": f"<{_MESSAGE_METADATA_TAG}>{payload}</{_MESSAGE_METADATA_TAG}>",
    }


def project_user_message_for_model(message: BaseMessage) -> BaseMessage:
    """为单次模型请求生成带私有元数据文本块的消息副本。"""
    if not isinstance(message, HumanMessage):
        return message
    metadata = _read_user_message_metadata(message)
    if metadata is None:
        return message

    metadata_block = _metadata_content_block(metadata)
    if isinstance(message.content, str):
        content: list[str | dict[str, Any]] = [
            metadata_block,
            {"type": "text", "text": message.content},
        ]
    elif isinstance(message.content, list):
        content = [metadata_block, *message.content]
    else:
        logger.warning(f"用户消息内容类型无效: message_id={message.id}")
        return message

    return message.model_copy(update={"content": cast(Any, content)})


def _project_request(request: ModelRequest[Any]) -> ModelRequest[Any]:
    """返回仅在本次模型调用中投影私有元数据的请求副本。"""
    messages = [project_user_message_for_model(message) for message in request.messages]
    return request.override(messages=cast("list[AnyMessage]", messages))


class UserMessageMetadataMiddleware(AgentMiddleware[Any, Any, Any]):
    """在 Planner 模型调用前临时投影用户消息私有元数据。"""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """投影同步模型请求。"""
        return handler(_project_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """投影异步模型请求。"""
        return await handler(_project_request(request))
