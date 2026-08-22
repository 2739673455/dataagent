"""会话标题生成服务"""

from __future__ import annotations

import asyncio
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from app.agents.manager import agent_manager
from app.repositories.conversation_pg_repo import ConversationPGRepo

_DEFAULT_TITLE = "新对话"
_MAX_TITLE_LENGTH = 64
_MAX_MODEL_INPUT_LENGTH = 4_000
_TITLE_PROMPT = """根据用户的首条消息生成一个准确、简洁的中文会话标题
只输出标题文本，不要解释，不要添加引号、书名号、Markdown 或“标题”前缀
标题不得超过 30 个字符
"""
_TITLE_WRAPPERS = "`\"'“”‘’《》「」『』"


def initial_conversation_title(user_text: str | None) -> str:
    """用首条用户文本生成即时标题"""
    if not user_text or not (normalized := user_text.strip()):
        return _DEFAULT_TITLE
    return normalized[:_MAX_TITLE_LENGTH]


def normalize_generated_title(raw_title: str) -> str:
    """规范化模型生成的标题"""
    title = " ".join(raw_title.split()).strip(_TITLE_WRAPPERS).strip()
    for prefix in ("标题：", "标题:"):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break
    return title.strip(_TITLE_WRAPPERS).strip()[:_MAX_TITLE_LENGTH]


class ConversationTitleService:
    """在后台生成并安全更新会话标题"""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(
        self,
        conversation_repo: ConversationPGRepo,
        user_id: int,
        conversation_id: UUID,
        expected_title: str,
        user_text: str,
    ) -> None:
        """启动受管理的后台标题生成任务"""
        task = asyncio.create_task(
            self.generate_and_update(
                conversation_repo,
                user_id,
                conversation_id,
                expected_title,
                user_text,
            ),
            name=f"conversation-title:{user_id}:{conversation_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def generate_and_update(
        self,
        conversation_repo: ConversationPGRepo,
        user_id: int,
        conversation_id: UUID,
        expected_title: str,
        user_text: str,
    ) -> None:
        """调用主模型生成标题并避免覆盖用户修改"""
        try:
            model = await agent_manager.get_active_model()
            response = await model.ainvoke(
                [
                    SystemMessage(content=_TITLE_PROMPT),
                    HumanMessage(content=user_text[:_MAX_MODEL_INPUT_LENGTH]),
                ]
            )
            generated_title = normalize_generated_title(response.text)
            if not generated_title:
                return

            conversation = await conversation_repo.get(user_id, conversation_id)
            if conversation is None or conversation.title != expected_title:
                return
            await conversation_repo.update(
                conversation,
                title=generated_title,
                title_pending=False,
            )
            logger.info(
                f"会话标题生成成功: user_id={user_id}, "
                f"conversation_id={conversation_id}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                f"会话标题生成失败: user_id={user_id}, "
                f"conversation_id={conversation_id}"
            )

    async def close(self) -> None:
        """取消并回收未完成的标题生成任务"""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


conversation_title_service = ConversationTitleService()
