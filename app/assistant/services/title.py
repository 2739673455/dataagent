"""会话标题生成与更新。"""

from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage

from app.assistant.model_factory import create_configured_model
from app.assistant.repositories.conversation import ConversationPGRepo
from app.assistant.resources import TITLE_PROMPT
from app.shared.clients.postgres_client_manager import PostgresClientManager
from app.shared.config.app_config import cfg

_MAX_MODEL_INPUT_LENGTH = 4_000


class ConversationTitleService:
    """生成并保存会话标题。"""

    def __init__(self, postgres: PostgresClientManager) -> None:
        self._postgres = postgres

    async def generate_and_update(
        self, user_id: int, conversation_id: UUID, user_text: str
    ) -> None:
        """调用模型生成标题，输出非空时更新会话。"""
        async with create_configured_model(cfg.lm_config.active) as model:
            response = await model.ainvoke(
                [
                    SystemMessage(content=TITLE_PROMPT),
                    HumanMessage(content=user_text[:_MAX_MODEL_INPUT_LENGTH]),
                ]
            )
        title = " ".join(response.text.split())[:64]
        if not title:
            return

        async with self._postgres.session() as session:
            await ConversationPGRepo(session).update(
                user_id,
                conversation_id,
                title=title,
            )
            await session.commit()
