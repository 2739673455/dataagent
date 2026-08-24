"""Celery 同步任务中的异步运行辅助"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """为单个 Celery 任务运行异步领域逻辑"""
    return asyncio.run(coroutine)
