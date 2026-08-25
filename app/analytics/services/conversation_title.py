"""会话标题生成服务"""

from __future__ import annotations

from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.analytics.repositories.conversation import ConversationPGRepo

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
    """生成并安全更新会话标题"""

    def __init__(self, model: BaseChatModel) -> None:
        """初始化用于生成标题的语言模型"""
        self._model = model

    async def generate_and_update(
        self,
        conversation_repo: ConversationPGRepo,
        user_id: int,
        conversation_id: UUID,
        expected_title: str,
        user_text: str,
    ) -> None:
        """调用主模型生成标题并避免覆盖用户修改"""
        response = await self._model.ainvoke(
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
        await conversation_repo.complete_title_generation(
            conversation,
            title=generated_title,
        )
