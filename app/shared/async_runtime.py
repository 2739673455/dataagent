"""同步入口使用的事件循环工厂和异步运行辅助。"""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def create_event_loop() -> asyncio.AbstractEventLoop:
    """Windows 使用兼容 Psycopg 的 Selector，其余平台使用默认循环。"""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """在独立且兼容数据库驱动的事件循环中运行异步逻辑。"""
    return asyncio.run(coroutine, loop_factory=create_event_loop)
