from .auth_exception_handler import register_auth_exception_handlers
from .chat_exception_handler import register_chat_exception_handlers
from .conversation_exception_handler import register_conversation_exception_handlers
from .model_config_exception_handler import register_model_config_exception_handlers
from .user_exception_handler import register_user_exception_handlers


def register_exception_handlers(app):
    register_auth_exception_handlers(app)
    register_user_exception_handlers(app)
    register_model_config_exception_handlers(app)
    register_conversation_exception_handlers(app)
    register_chat_exception_handlers(app)
