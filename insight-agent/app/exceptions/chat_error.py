from app.exceptions.base import NotFoundError


class ConversationNotFound(NotFoundError):
    code = 1401
    message = "对话不存在"
