import asyncio
from collections.abc import AsyncIterator

from app.agent.agent import get_agent, get_workspace_dir
from app.core.database import get_db_session
from app.entities.chat import ContextCompaction
from app.mappers import message_mapper
from app.repositories import context_compaction_repo, conversation_repo, message_repo
from app.schemas import chat_schema
from langchain_core.runnables import RunnableConfig
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


async def load_conversation_context(
    conversation_id: int, user_id: int
) -> tuple[list[dict], int, bool] | None:
    """
    加载会话初始上下文：校验会话归属、加载历史消息、应用上下文压缩。

    返回 (messages, cur_context_seq, is_draft)。
    会话不存在或不属于当前用户时返回 None。
    """
    async with get_db_session() as db_session:
        # ========= 检查会话归属 =========
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get_by_id(db_session, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None

        # 判断是否为草稿会话
        is_draft = conversation.is_draft == 1

        # ========= 加载历史消息 =========
        # 从数据库加载历史消息
        message_entities = await message_repo.ls(db_session, conversation_id)
        # 获取最后一个消息的 context_seq；若没有历史消息，则将 context_seq 设置为 -1
        cur_context_seq = message_entities[-1].context_seq if message_entities else -1
        # 将历史消息转换为 LangChain Message
        messages = [
            message_mapper.schema_to_langchain_message(
                message_mapper.entity_to_schema(i),
                user_id=user_id,
                conversation_id=conversation_id,
            )
            for i in message_entities
        ]

        # ======== 应用上下文压缩 =========
        # 从数据库加载最新压缩上下文
        context_compaction_entity = (
            await context_compaction_repo.get_latest_by_conversation_id(
                db_session, conversation_id
            )
        )
        # 如果存在压缩上下文，则替换历史消息前缀
        if context_compaction_entity:
            messages[: context_compaction_entity.end_seq] = [
                {"role": "user", "content": context_compaction_entity.summary_message}
            ]

    return messages, cur_context_seq, is_draft


async def _add_message(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    message: chat_schema.MessageSchema,
):
    """将消息写入数据库与消息列表，并同步刷新对话更新时间"""
    # 存储到数据库
    message_entity = message_mapper.schema_to_entity(message, conversation_id)
    await message_repo.create(db_session, message_entity)
    # 追加到内存消息列表
    messages.append(
        message_mapper.schema_to_langchain_message(
            message, user_id=user_id, conversation_id=conversation_id
        )
    )
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)


async def stream_chat(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[chat_schema.MessageSchema]:
    """基于当前历史消息处理一轮聊天并流式返回响应"""
    logger.info(f"{conversation_id=}: {user_message=}")

    await _add_message(db_session, user_id, conversation_id, messages, user_message)

    # 获取最后一个消息的 context_seq
    cur_context_seq = user_message.context_seq or 0
    # 绝对顺序号（context_seq）与运行时下标的偏移量，用于将 cutoff_index 换算为 end_seq
    seq_offset = cur_context_seq - len(messages) + 1

    last_cutoff_index: int | None = None
    last_summary: str | None = None

    # 【变量说明】
    # - cutoff_index: Agent state 中模型可见范围的起始索引（state[:cutoff_index] 被摘要），直接对应 messages 下标
    # - end_seq = seq_offset + cutoff_index: 压缩的消息 context_seq 范围是 [0, end_seq)，不包含 end_seq
    #
    # 【例：单次压缩】
    # messages = [0, 1, 2, 3, 4, 5] (6条)
    # cur_context_seq=5, seq_offset = 5 - 6 + 1 = 0
    # cutoff_index=3 (即 state.messages[:3] 被摘要)
    # 结束后: messages[:3] = [summary] → messages = [summary, 3, 4, 5]
    # end_seq = 0 + 3 = 3 (0,1,2 被摘要)

    # 获取工作区路径
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    # 将工作区路径写入运行时配置
    config = RunnableConfig(configurable={"workspace_dir": str(workspace_dir)})
    # 获取 Agent 实例
    agent = await get_agent()

    # 当前 chunk 产生的压缩记录，消息写入后随即入库
    pending_compaction: ContextCompaction | None = None

    async for chunk in agent.astream(input={"messages": messages}, config=config):
        # 收到 cancel 信号时停止
        if cancel is not None and cancel.is_set():
            logger.info(f"{conversation_id=}: agent cancelled")
            break

        logger.info(f"{conversation_id=}: agent_response={chunk}")

        # ========== 处理上下文压缩 ==========
        if "model" in chunk and "_summarization_event" in chunk["model"]:
            # 获取压缩事件，从中获取 cutoff_index 和 summary_message
            summarization_event = chunk["model"]["_summarization_event"]
            cutoff_index = summarization_event["cutoff_index"]
            summary_payload = summarization_event["summary_message"]
            summary_message = (
                summary_payload.content
                if hasattr(summary_payload, "content")
                else str(summary_payload)
            )
            logger.info(f"{conversation_id=}: {summary_message=}")

            # 记录 end_seq 用于下次加载时重构消息列表
            end_seq = seq_offset + cutoff_index
            pending_compaction = ContextCompaction(
                conversation_id=conversation_id,
                end_seq=end_seq,
                summary_message=summary_message,
            )

            # 记录替换参数，Agent 输出完毕后再统一应用到消息列表
            last_cutoff_index = cutoff_index
            last_summary = summary_message

        # 将 agent 输出的模型消息和工具消息转换为 MessageSchema 列表
        responses = message_mapper.agent_chunk_to_schemas(chunk)
        if responses:
            for response in responses:
                cur_context_seq += 1
                response.context_seq = cur_context_seq
                await _add_message(
                    db_session, user_id, conversation_id, messages, response
                )
                # 返回 Agent 响应
                yield response

        # 消息写入后再写压缩记录，此时 end_seq 指向的历史消息已全部落库
        if pending_compaction is not None:
            await context_compaction_repo.create(db_session, pending_compaction)
            pending_compaction = None

    # Agent 输出完毕，应用最后一次压缩到消息列表
    if last_cutoff_index is not None and last_summary is not None:
        messages[:last_cutoff_index] = [{"role": "user", "content": last_summary}]
