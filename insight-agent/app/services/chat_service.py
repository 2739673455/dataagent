from collections.abc import AsyncIterator

import openai
from app.core.agent import get_agent, get_workspace_dir
from app.entities.chat import ContextCompaction
from app.mappers import message_mapper
from app.repositories import context_compaction_repo, conversation_repo, message_repo
from app.schemas import chat_schema
from langchain_core.runnables import RunnableConfig
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


async def _add_message(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    message: chat_schema.MessageSchema,
):
    """将消息写入会话上下文与数据库，并同步刷新对话更新时间"""
    # 将消息添加到消息列表
    messages.append(
        message_mapper.schema_to_langchain_message(
            message, user_id=user_id, conversation_id=conversation_id
        )
    )
    # 存储到数据库
    message_entity = message_mapper.schema_to_entity(message, conversation_id)
    await message_repo.create(db_session, message_entity)
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)


async def stream_chat(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
    has_applied_summary: bool,
) -> AsyncIterator[chat_schema.MessageSchema]:
    """基于当前历史消息处理一轮聊天并流式返回响应"""
    logger.info(f"{conversation_id=}: {user_message=}")

    await _add_message(db_session, user_id, conversation_id, messages, user_message)

    # 当前已落库的最后一条消息的上下文顺序号（0-based）
    cur_context_seq = user_message.context_seq or 0
    # 压缩后的纯文本摘要，用于替换运行时上下文并落库
    summary_message = ""
    # 防止同一轮中相同 cutoff_index 被重复写入 context_compaction
    last_saved_cutoff_index = -1
    # 绝对顺序号与运行时下标的偏移量，用于将 cutoff_index 换算为 end_seq
    seq_offset = cur_context_seq - (len(messages) - 1)
    # 当前运行时 messages 已经应用到的累计压缩边界，用于多次压缩时按增量替换
    applied_cutoff_index = 0

    # 调用 Agent
    try:
        workspace_dir = get_workspace_dir(user_id, conversation_id)
        config = RunnableConfig(configurable={"workspace_dir": str(workspace_dir)})
        agent = await get_agent()

        async for chunk in agent.astream(input={"messages": messages}, config=config):
            logger.info(f"{conversation_id=}: agent_response={chunk}")

            # 获取压缩后的上下文
            if "model" in chunk and "_summarization_event" in chunk["model"]:
                summarization_event = chunk["model"]["_summarization_event"]
                cutoff_index = summarization_event["cutoff_index"]
                summary_payload = summarization_event["summary_message"]
                summary_message = (
                    summary_payload.content
                    if hasattr(summary_payload, "content")
                    else str(summary_payload)
                )
                logger.info(f"{conversation_id=}: {summary_message=}")

                # 将历史消息替换为压缩上下文
                replace_cutoff_index = (
                    cutoff_index
                    - applied_cutoff_index
                    + (1 if has_applied_summary else 0)
                )
                messages[:replace_cutoff_index] = [
                    {"role": "user", "content": summary_message}
                ]
                applied_cutoff_index = cutoff_index
                has_applied_summary = True

                # 将压缩上下文存入数据库
                if cutoff_index != last_saved_cutoff_index:
                    end_seq = seq_offset + cutoff_index
                    compaction = ContextCompaction(
                        conversation_id=conversation_id,
                        end_seq=end_seq,
                        summary_message=summary_message,
                    )
                    await context_compaction_repo.create(db_session, compaction)
                    last_saved_cutoff_index = cutoff_index

            responses = message_mapper.agent_chunk_to_schemas(chunk)
            if not responses:
                continue

            for response in responses:
                cur_context_seq += 1
                response.context_seq = cur_context_seq
                await _add_message(
                    db_session, user_id, conversation_id, messages, response
                )
                # 返回 Agent 响应
                yield response

    except openai.NotFoundError as e:
        if "No endpoints found that support image input" not in e.message:
            raise

        # 处理模型不支持图片输入的情况
        cur_context_seq += 1
        text = "当前模型不支持图片输入。"
        response = chat_schema.MessageSchema(
            role="assistant",
            parts=[chat_schema.TextContent(text=text)],
            finish_reason="stop",
            context_seq=cur_context_seq,
        )
        await _add_message(db_session, user_id, conversation_id, messages, response)
        # 返回 Agent 响应
        yield response
