"""QuickJS 跨线程事件循环执行器"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import importlib
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from types import TracebackType
from typing import Any, Self

_HEARTBEAT_SECONDS = 0.01
_PROBE_TIMEOUT_SECONDS = 0.05
_PROBE_STOP_SECONDS = 0.1


class ResponsiveThreadWorker:
    """在跨线程 selector 唤醒异常时保持 QuickJS worker 可调度"""

    def __init__(self, name: str = "quickjs-worker") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        if self._loop is not None:
            return
        with self._start_lock:
            if self._loop is not None:
                return

            def runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop

                def heartbeat() -> None:
                    if not loop.is_closed():
                        loop.call_later(_HEARTBEAT_SECONDS, heartbeat)

                loop.call_later(_HEARTBEAT_SECONDS, heartbeat)
                self._ready.set()
                try:
                    loop.run_forever()
                finally:
                    with contextlib.suppress(Exception):
                        pending = [
                            task for task in asyncio.all_tasks(loop) if not task.done()
                        ]
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                    loop.close()

            self._thread = threading.Thread(
                target=runner,
                name=self._name,
                daemon=True,
            )
            self._thread.start()
            self._ready.wait()

    def run_sync(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """提交协程并同步等待 worker 返回"""
        self._ensure_started()
        if self._loop is None:
            raise RuntimeError("QuickJS worker event loop did not start")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Future[Any]:
        """提交协程并在调用方事件循环等待结果"""
        self._ensure_started()
        if self._loop is None:
            raise RuntimeError("QuickJS worker event loop did not start")
        caller_loop = asyncio.get_running_loop()
        worker_future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self._keep_caller_loop_responsive(caller_loop, worker_future)
        return asyncio.wrap_future(worker_future)

    @staticmethod
    def _keep_caller_loop_responsive(
        caller_loop: asyncio.AbstractEventLoop,
        worker_future: Future[Any],
    ) -> None:
        """在 worker 执行期间为调用方 selector 提供有界唤醒点"""

        def heartbeat() -> None:
            if not worker_future.done():
                caller_loop.call_later(_HEARTBEAT_SECONDS, heartbeat)

        caller_loop.call_later(_HEARTBEAT_SECONDS, heartbeat)

    def close(self) -> None:
        """在所属线程释放 QuickJS 对象并停止 worker"""
        if self._loop is None or self._thread is None:
            return

        async def collect() -> None:
            gc.collect()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(collect(), self._loop).result(timeout=1)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop = None
        self._thread = None

    def __enter__(self) -> Self:
        self._ensure_started()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        self._ensure_started()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _cross_thread_wakeup_is_responsive() -> bool:
    """探测 call_soon_threadsafe 能否及时唤醒 selector"""
    ready = threading.Event()
    callback_ran = threading.Event()
    loop_holder: list[asyncio.AbstractEventLoop] = []

    def runner() -> None:
        loop = asyncio.new_event_loop()
        loop_holder.append(loop)
        loop.call_later(_PROBE_STOP_SECONDS, loop.stop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=runner, name="quickjs-wakeup-probe", daemon=True)
    thread.start()
    ready.wait()
    loop_holder[0].call_soon_threadsafe(callback_ran.set)
    responsive = callback_ran.wait(_PROBE_TIMEOUT_SECONDS)
    thread.join(timeout=_PROBE_STOP_SECONDS * 2)
    return responsive


def install_responsive_quickjs_worker() -> bool:
    """按运行环境探测结果安装 QuickJS worker 回退实现"""
    if _cross_thread_wakeup_is_responsive():
        return False
    repl_module = importlib.import_module("langchain_quickjs._repl")
    vars(repl_module)["ThreadWorker"] = ResponsiveThreadWorker
    return True
