from app.core.exceptions.base import NotFoundError


class ConversationNotFoundError(NotFoundError):
    type = "conversation-not-found"
    title = "对话不存在"
