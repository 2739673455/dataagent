import asyncio
import json
from collections.abc import Sequence

from openai import (
    APIError as OpenAIError,
)
from openai import (
    AuthenticationError as OpenAIAuthenticationError,
)
from openai import (
    BadRequestError as OpenAIBadRequestError,
)
from openai import (
    InternalServerError as OpenAIInternalError,
)
from openai import (
    NotFoundError as OpenAINotFoundError,
)
from openai import (
    RateLimitError as OpenAIRateLimitError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.chat import Message
from app.schemas.chat import MessageItem
from app.utils.call_model import call_model, stream_model
from app.utils.cos import extract_cos_key, get_get_presigned_url
from app.utils.log import app_logger


async def get_messages(
    db_session: AsyncSession, conversation_id: int
) -> Sequence[Message]:
    """获取消息列表"""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.timestamp.asc())
    )
    result = await db_session.execute(stmt)
    messages = result.scalars().all()
    for message in messages:  # 将json字符串转换为str或list[dict]
        message.content = json.loads(message.content)
    return messages


async def image_url_to_get_presigned_url(messages: Sequence[Message | MessageItem]):
    """处理消息中的 cos_url 或 旧的预签名url 为 新的为预签名下载url"""
    tasks = []
    c_dicts = []  # 存储对应的 c_dict，用于后续更新
    for message in messages:
        if message.role == "user" and isinstance(message.content, list):
            for c_dict in message.content:
                if "image_url" in c_dict:
                    # 提取cos_key
                    cos_key = extract_cos_key(c_dict["image_url"])
                    # 获取预签名下载url
                    tasks.append(get_get_presigned_url(cos_key))
                    c_dicts.append(c_dict)
    if tasks:
        results = await asyncio.gather(*tasks)
        for c_dict, presinged_url in zip(c_dicts, results):
            c_dict["image_url"] = presinged_url


async def image_url_to_cos_url(messages: Sequence[MessageItem]):
    """处理消息中的图片url 为 cos_url"""
    for message in messages:
        if isinstance(message.content, list):
            for c_dict in message.content:
                if "image_url" in c_dict:
                    # 提取cos_key
                    cos_key = extract_cos_key(c_dict["image_url"])
                    c_dict["image_url"] = "cos://" + cos_key


async def _save_message_in_db(
    db_session: AsyncSession,
    last_message: MessageItem,
    user_id: int,
    conversation_id: int,
) -> Message:
    """保存消息到数据库"""
    message = Message(
        user_id=user_id,
        conversation_id=conversation_id,
        role=last_message.role,
        content=json.dumps(
            last_message.content, ensure_ascii=False
        ),  # 将str或list[dict]转换为json字符串
    )
    db_session.add(message)
    try:
        await db_session.commit()
        await db_session.refresh(message)
    except Exception:
        await db_session.rollback()
        raise
    return message


async def stream_response(
    conversation_id: int,
    user_id: int,
    messages: list[MessageItem],
    base_url: str,
    model_name: str | None,
    api_key: str | None,
    params: dict | None,
    db_session: AsyncSession,
):
    """流式返回AI回复"""
    try:
        app_logger.info(f"Received messages ({len(messages)})")
        # 转换图片url为cos_url
        await image_url_to_cos_url(messages)
        # 用户消息存入数据库
        user_message_id = messages[-1].message_id
        if not user_message_id:  # 如果没有消息id才存入数据库
            user_message = await _save_message_in_db(
                db_session, messages[-1], user_id, conversation_id
            )
            user_message_id = user_message.id
        # 转换cos_url为预签名下载url
        await image_url_to_get_presigned_url(messages)

        # 返回用户消息id
        yield (
            json.dumps({"type": "user_message_id", "user_message_id": user_message_id})
            + "\n"
        )
        app_logger.info(f"User message id: {user_message_id}")

        # 流式调用模型
        chunks: list[str] = []
        async for chunk in stream_model(
            messages, base_url, model_name, api_key, params
        ):
            chunks.append(chunk)
            yield (
                json.dumps({"type": "ai_chunk", "content": chunk}, ensure_ascii=False)
                + "\n"
            )

        # AI回复存入数据库
        ai_message = await _save_message_in_db(
            db_session,
            MessageItem(role="assistant", content="".join(chunks)),
            user_id,
            conversation_id,
        )

        # 发送完成信号，返回AI消息id
        yield (json.dumps({"type": "complete", "ai_message_id": ai_message.id}) + "\n")
        app_logger.info(f"AI message id: {ai_message.id}")

    except (
        OpenAINotFoundError,
        OpenAIBadRequestError,
        OpenAIAuthenticationError,
        OpenAIRateLimitError,
        OpenAIInternalError,
        OpenAIError,
    ) as e:
        app_logger.error(f"OpenAI API error: {e}")
        yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"
    except Exception as e:
        app_logger.error(f"Unexpected error in stream_response: {e}")
        yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"


async def generate_title(
    content: str | list[dict],
    base_url: str,
    model_name: str | None,
    api_key: str | None,
):
    """生成对话标题"""
    return await call_model(
        [
            {
                "role": "system",
                "content": "你需要为下面的用户提问生成一句简短的概括性标题，字数尽量在20字以内，不要带有句号",
            },
            {"role": "user", "content": content},
        ],
        base_url,
        model_name,
        api_key,
        None,
    )
