"""会话标题生成服务。"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.assistant.resource_loader import load_prompt

_DEFAULT_TITLE = "新对话"
_MAX_TITLE_LENGTH = 64
_MAX_MODEL_INPUT_LENGTH = 4_000
_TITLE_WRAPPERS = "`\"'“”‘’《》「」『』"


def initial_conversation_title(user_text: str | None) -> str:
    """用首条用户文本生成即时标题。"""
    if not user_text or not (normalized := user_text.strip()):
        return _DEFAULT_TITLE
    return normalized[:_MAX_TITLE_LENGTH]


def _normalize_generated_title(raw_title: str) -> str:
    """规范化模型生成的标题。"""
    title = " ".join(raw_title.split()).strip(_TITLE_WRAPPERS).strip()
    for prefix in ("标题：", "标题:"):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break
    return title.strip(_TITLE_WRAPPERS).strip()[:_MAX_TITLE_LENGTH]


class ConversationTitleService:
    """生成并整理会话标题。"""

    def __init__(self, model: BaseChatModel) -> None:
        """初始化用于生成标题的语言模型。"""
        self._model = model

    async def generate(self, user_text: str) -> str | None:
        """调用主模型生成标题，空输出不覆盖即时标题。"""
        response = await self._model.ainvoke(
            [
                SystemMessage(
                    content=load_prompt("conversation_title").format(
                        max_title_length=_MAX_TITLE_LENGTH
                    )
                ),
                HumanMessage(content=user_text[:_MAX_MODEL_INPUT_LENGTH]),
            ]
        )
        return _normalize_generated_title(response.text) or None
