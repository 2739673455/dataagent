"""对话管理异常"""


class ConversationError(Exception): ...


class ConversationNotFoundError(ConversationError):
    def __init__(self, message: str = "对话不存在"):
        super().__init__(message)
