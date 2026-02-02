from contextvars import ContextVar
from typing import Optional

# 定义一个 ContextVar 来存储当前线程的会话目录
# 默认值为 None
_session_dir_ctx: ContextVar[Optional[str]] = ContextVar("session_dir", default=None)
_thread_id_ctx: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)

def set_session_context(path: str):
    """设置当前线程的会话目录上下文"""
    return _session_dir_ctx.set(path)

def get_session_context() -> Optional[str]:
    """获取当前线程的会话目录上下文"""
    return _session_dir_ctx.get()

def set_thread_context(thread_id: str):
    """设置当前线程的 Thread ID 上下文"""
    return _thread_id_ctx.set(thread_id)

def get_thread_context() -> Optional[str]:
    """获取当前线程的 Thread ID 上下文"""
    return _thread_id_ctx.get()

def reset_session_context(session_token, thread_token=None):
    """重置上下文"""
    _session_dir_ctx.reset(session_token)
    if thread_token:
        _thread_id_ctx.reset(thread_token)
