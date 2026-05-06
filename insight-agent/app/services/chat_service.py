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
    加载对话初始上下文：校验对话归属、加载历史消息、应用压缩上下文。

    返回 (messages, cur_context_seq, is_draft)。
    对话不存在或不属于当前用户时返回 None。
    """
    async with get_db_session() as db_session:
        # ========= 检查对话归属 =========
        # 检查对话是否存在且属于当前用户
        conversation = await conversation_repo.get_by_id(db_session, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        # 标记是否为草稿对话
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

        # ======== 应用压缩上下文 =========
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
    # 消息入库
    message_entity = message_mapper.schema_to_entity(message, conversation_id)
    await message_repo.create(db_session, message_entity)
    # 追加内存消息列表
    messages.append(
        message_mapper.schema_to_langchain_message(
            message, user_id=user_id, conversation_id=conversation_id
        )
    )
    # 刷新对话更新时间
    await conversation_repo.touch_update_at(db_session, conversation_id)


async def _execute_agent(
    messages: list[dict],
    user_id: int,
    conversation_id: int,
    cancel: asyncio.Event,
) -> AsyncIterator[dict]:
    """执行 Agent 并流式返回原始 chunk"""
    # 获取并确保用户会话工作区目录存在
    workspace_dir = get_workspace_dir(user_id, conversation_id)
    # 创建 Agent 运行配置
    config = RunnableConfig(configurable={"workspace_dir": str(workspace_dir)})
    # 获取 Agent 实例
    agent = await get_agent()

    async for chunk in agent.astream(input={"messages": messages}, config=config):
        # 收到取消信号则跳出循环
        if cancel.is_set():
            logger.info(f"{conversation_id=}: agent cancelled")
            break
        yield chunk


async def run_agent_turn(
    db_session: AsyncSession,
    user_id: int,
    conversation_id: int,
    messages: list[dict],
    user_message: chat_schema.MessageSchema,
    cancel: asyncio.Event,
    max_retries: int = 5,
) -> AsyncIterator[chat_schema.MessageSchema]:
    """
    执行一轮 Agent 对话并流式返回响应。

    当 Agent 因无工具调用而退出但 finish_reason != "stop" 时
    （如模型被截断或未正常结束），自动将消息重新输入 Agent 继续。
    """
    logger.info(f"{conversation_id=}: {user_message=}")

    # 消息入库，同时追加到内存消息列表
    await _add_message(db_session, user_id, conversation_id, messages, user_message)

    # 获取最新消息的 context_seq
    cur_context_seq = user_message.context_seq or 0

    for _ in range(max_retries + 1):
        # 消息列表中消息的 context_seq 与消息索引之间的偏移量
        seq_offset = cur_context_seq - len(messages) + 1
        last_finish_reason: str | None = None  # 记录模型最后一条消息的 finish_reason
        last_cutoff_index: int | None = None  # 记录最后一次压缩事件的截断位置
        last_summary: str | None = None  # 记录最后一次压缩事件的摘要内容
        pending_compaction: ContextCompaction | None = None  # 待写入数据库的压缩上下文

        async for chunk in _execute_agent(messages, user_id, conversation_id, cancel):
            # 收到取消信号则跳出循环
            if cancel.is_set():
                break

            logger.info(f"{conversation_id=}: agent_response={chunk}")

            # ========== 处理上下文压缩 ==========
            # cutoff_index: Agent state 中模型可见范围的起始索引（state[:cutoff_index] 被摘要），直接对应 messages 下标
            # end_seq = seq_offset + cutoff_index: 压缩的消息 context_seq 范围是 [0, end_seq)，不包含 end_seq
            #
            # 例1（无压缩前文）: messages=[0,1,2,3,4,5], cur_context_seq=5, len=6
            # → seq_offset=5-6+1=0, cutoff_index=3 → end_seq=3, 结束后 messages=[summary,3,4,5]
            #
            # 例2（已有压缩前文）: messages=[summary,3,4,5,6,7], cur_context_seq=7, len=6
            # → seq_offset=7-6+1=2, cutoff_index=3 → end_seq=5 (context_seq 0..4 被摘要)
            if "model" in chunk and "_summarization_event" in chunk["model"]:
                # 获取上下文压缩事件
                summarization_event = chunk["model"]["_summarization_event"]
                # 获取压缩截止位置
                cutoff_index = summarization_event["cutoff_index"]
                # 获取摘要内容
                summary_payload = summarization_event["summary_message"]
                summary_message = (
                    summary_payload.content
                    if hasattr(summary_payload, "content")
                    else str(summary_payload)
                )

                logger.info(f"{conversation_id=}: {summary_message=}")

                # 计算对应全量消息的压缩截止位置
                end_seq = seq_offset + cutoff_index

                # 记录待入库的压缩上下文
                pending_compaction = ContextCompaction(
                    conversation_id=conversation_id,
                    end_seq=end_seq,
                    summary_message=summary_message,
                )

                # 更新压缩的截止位置和摘要内容
                last_cutoff_index = cutoff_index
                last_summary = summary_message

            # 将 agent 输出的模型消息和工具消息转换为 MessageSchema 列表
            responses = message_mapper.agent_chunk_to_schemas(chunk)
            if responses:
                for response in responses:
                    cur_context_seq += 1  # 递增 context_seq
                    response.context_seq = cur_context_seq
                    # 消息入库，同时追加到内存消息列表
                    await _add_message(
                        db_session, user_id, conversation_id, messages, response
                    )
                    # 记录模型最后一条消息的 finish_reason
                    last_finish_reason = response.finish_reason
                    yield response

            # 写入压缩记录
            if pending_compaction is not None:
                await context_compaction_repo.create(db_session, pending_compaction)
                pending_compaction = None

        # 应用最后一次压缩到消息列表
        if last_cutoff_index is not None and last_summary is not None:
            messages[:last_cutoff_index] = [{"role": "user", "content": last_summary}]

        # 模型正常结束或用户取消则退出 retry 循环
        if cancel.is_set() or last_finish_reason == "stop":
            break

    logger.info(f"{conversation_id=}: agent finished")
