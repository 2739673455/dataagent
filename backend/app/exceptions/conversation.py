from app.exceptions.base import NotFoundError


class ConversationNotFoundError(NotFoundError):
    code = 1402
    message = "对话不存在"
