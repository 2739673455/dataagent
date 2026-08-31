"""Specialist 单次 Agent Run 的 Shell Job 运行时"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import secrets
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict

from app.sandbox.backend import (
    DockerSandboxBackend,
    SandboxShellJobCancellation,
    SandboxShellJobExecution,
)

type ShellJobStatus = Literal[
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_FOREGROUND_WAIT_SECONDS = 60.0
_CLEANUP_WAIT_SECONDS = 8.0


class ShellJobResult(BaseModel):
    """Shell Job 对模型公开的当前或最终结果"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: ShellJobStatus
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float
    exit_code: int | None = None
    output: str | None = None
    output_path: str
    output_truncated: bool = False
    output_inline_truncated: bool = False
    workspace_limit_exceeded: bool = False
    reviewed_at: datetime | None = None
    error: str | None = None


class ShellJobSummary(BaseModel):
    """Shell Job 列表项"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: ShellJobStatus
    command: str
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float
    exit_code: int | None = None
    output_path: str
    output_truncated: bool = False
    workspace_limit_exceeded: bool = False
    reviewed_at: datetime | None = None


class ShellJobError(BaseModel):
    """Shell Job 工具的稳定错误结构"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    code: Literal["job_not_found", "invalid_request"]
    message: str


@dataclass(slots=True)
class _ShellJobRecord:
    """Registry 内部保存的单个 Shell Job 可变记录"""

    job_id: str
    command: str
    started_at: datetime
    output_path: str
    done: asyncio.Event
    started: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    status: ShellJobStatus = "running"
    finished_at: datetime | None = None
    exit_code: int | None = None
    output: str | None = None
    output_truncated: bool = False
    output_inline_truncated: bool = False
    workspace_limit_exceeded: bool = False
    reviewed_at: datetime | None = None
    error: str | None = None
    cancel_requested: bool = False
    cancel_confirmed: bool = False
    cancel_resolved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    cancel_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    monitor_task: asyncio.Task[None] | None = field(default=None, repr=False)


def _elapsed_seconds(record: _ShellJobRecord, now: datetime | None = None) -> float:
    """计算任务从实际启动到当前或终态的耗时"""
    endpoint = record.finished_at or now or datetime.now(UTC)
    return round(max(0.0, (endpoint - record.started_at).total_seconds()), 3)


class ShellJobRuntime:
    """协调当前 Specialist Agent Run 独占的 Shell Job"""

    def __init__(
        self,
        backend: DockerSandboxBackend,
        *,
        foreground_wait_seconds: float = _FOREGROUND_WAIT_SECONDS,
    ) -> None:
        """绑定 Session Sandbox，并配置仅供测试缩短的前台等待时间"""
        if not math.isfinite(foreground_wait_seconds) or foreground_wait_seconds < 0:
            raise ValueError("foreground_wait_seconds 必须是非负有限数")
        self._backend = backend
        self._foreground_wait_seconds = foreground_wait_seconds
        self._records: dict[str, _ShellJobRecord] = {}
        self._lock = threading.RLock()
        self._cleanup_lock = asyncio.Lock()
        self._closing = False

    def _new_job_id(self) -> str:
        """生成当前 Registry 中唯一的模型可见标识"""
        with self._lock:
            while True:
                job_id = f"job_{secrets.token_hex(4)}"
                if job_id not in self._records:
                    return job_id

    @staticmethod
    def _result(
        record: _ShellJobRecord,
        *,
        include_output: bool,
    ) -> ShellJobResult:
        """从内部记录生成稳定公开结果"""
        return ShellJobResult(
            job_id=record.job_id,
            status=record.status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            elapsed_seconds=_elapsed_seconds(record),
            exit_code=record.exit_code,
            output=record.output if include_output else None,
            output_path=record.output_path,
            output_truncated=record.output_truncated,
            output_inline_truncated=record.output_inline_truncated,
            workspace_limit_exceeded=record.workspace_limit_exceeded,
            reviewed_at=record.reviewed_at,
            error=record.error,
        )

    @staticmethod
    def _summary(record: _ShellJobRecord) -> ShellJobSummary:
        """从内部记录生成列表摘要"""
        return ShellJobSummary(
            job_id=record.job_id,
            status=record.status,
            command=record.command,
            started_at=record.started_at,
            finished_at=record.finished_at,
            elapsed_seconds=_elapsed_seconds(record),
            exit_code=record.exit_code,
            output_path=record.output_path,
            output_truncated=record.output_truncated,
            workspace_limit_exceeded=record.workspace_limit_exceeded,
            reviewed_at=record.reviewed_at,
        )

    async def _monitor(self, record: _ShellJobRecord) -> None:
        """持有独立监控任务并把 Backend 结果提交为单一终态"""
        loop = asyncio.get_running_loop()

        def started_callback() -> None:
            """把监控线程观察到的实际启动事件投递回事件循环"""
            loop.call_soon_threadsafe(self._mark_started, record)

        try:
            execution = await self._backend.arun_shell_job(
                record.job_id,
                record.command,
                started_callback,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Shell Job 监控失败: job_id={record.job_id}")
            execution = SandboxShellJobExecution(
                status="interrupted",
                error=str(exc).strip() or type(exc).__name__,
            )

        with self._lock:
            wait_for_cancellation = record.cancel_requested
        if wait_for_cancellation:
            await record.cancel_resolved.wait()

        with self._lock:
            if record.status in _TERMINAL_STATUSES:
                record.done.set()
                return
            if record.cancel_confirmed and execution.status in {"completed", "failed"}:
                status: ShellJobStatus = "cancelled"
            else:
                status = execution.status
            record.status = status
            record.finished_at = datetime.now(UTC)
            record.exit_code = execution.exit_code
            record.output = execution.output
            record.output_truncated = execution.output_truncated
            record.output_inline_truncated = execution.output_inline_truncated
            record.workspace_limit_exceeded = execution.workspace_limit_exceeded
            record.error = execution.error
            record.done.set()

    def _mark_started(self, record: _ShellJobRecord) -> None:
        """在事件循环线程提交 Backend 已实际启动命令的状态"""
        with self._lock:
            if record.started.is_set() or record.status in _TERMINAL_STATUSES:
                return
            record.started_at = datetime.now(UTC)
            record.started.set()

    @staticmethod
    async def _wait_until_started_or_done(record: _ShellJobRecord) -> None:
        """等待命令实际启动；启动失败时由终态事件提前结束等待"""
        started_wait = asyncio.create_task(record.started.wait())
        done_wait = asyncio.create_task(record.done.wait())
        waits = {started_wait, done_wait}
        try:
            _, pending = await asyncio.wait(
                waits,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            pending = {task for task in waits if not task.done()}
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def execute(self, command: str) -> ShellJobResult:
        """立即启动命令，固定等待后返回终态或后台句柄"""
        if not command.strip():
            raise ValueError("Shell 命令不能为空")
        with self._lock:
            if self._closing:
                raise RuntimeError("Shell Job Runtime 正在关闭")
            job_id = self._new_job_id()
            record = _ShellJobRecord(
                job_id=job_id,
                command=command,
                started_at=datetime.now(UTC),
                output_path=f"large_tool_results/shell_jobs/{job_id}.log",
                done=asyncio.Event(),
            )
            self._records[job_id] = record
            record.monitor_task = asyncio.create_task(
                self._monitor(record),
                name=f"shell-job-{job_id}",
            )

        await self._wait_until_started_or_done(record)
        if not record.done.is_set():
            try:
                await asyncio.wait_for(
                    asyncio.shield(record.done.wait()),
                    timeout=self._foreground_wait_seconds,
                )
            except TimeoutError:
                with self._lock:
                    return self._result(record, include_output=False)

        with self._lock:
            if record.status in _TERMINAL_STATUSES:
                record.reviewed_at = datetime.now(UTC)
            return self._result(record, include_output=True)

    def list(self, *, include_reviewed: bool = False) -> list[ShellJobSummary]:
        """列出运行中及默认尚未查看的任务，不改变 reviewed 状态"""
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.started_at)
            return [
                self._summary(record)
                for record in records
                if include_reviewed
                or record.status not in _TERMINAL_STATUSES
                or record.reviewed_at is None
            ]

    def _not_found(self, job_id: str) -> ShellJobError:
        """构造不泄露其他 Run 信息的未找到结果"""
        return ShellJobError(
            code="job_not_found",
            message=f"当前 Agent Run 中不存在 Shell Job: {job_id}",
        )

    async def get(
        self,
        job_id: str,
        *,
        wait_seconds: float = 0,
    ) -> ShellJobResult | ShellJobError:
        """立即查看任务，或在指定时限内等待其终态"""
        if not math.isfinite(wait_seconds) or wait_seconds < 0:
            raise ValueError("wait_seconds 必须是非负有限数")
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return self._not_found(job_id)
            is_terminal = record.status in _TERMINAL_STATUSES
        if not is_terminal and wait_seconds > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(record.done.wait()),
                    timeout=wait_seconds,
                )
        with self._lock:
            if record.status in _TERMINAL_STATUSES:
                record.reviewed_at = datetime.now(UTC)
            return self._result(record, include_output=False)

    async def _cancel(
        self,
        job_id: str,
        *,
        review_terminal: bool,
    ) -> ShellJobResult | ShellJobError:
        """串行提交一次进程组取消请求并处理自然结束竞态"""
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return self._not_found(job_id)
        async with record.cancel_lock:
            with self._lock:
                if record.status in _TERMINAL_STATUSES:
                    if review_terminal:
                        record.reviewed_at = datetime.now(UTC)
                    return self._result(record, include_output=False)
                record.cancel_requested = True
                record.cancel_resolved.clear()
                record.status = "cancelling"

            cancellation_task = asyncio.create_task(
                self._backend.acancel_shell_job(job_id)
            )
            caller_cancelled = False
            cancellation: SandboxShellJobCancellation | None = None
            cancellation_error: Exception | None = None
            try:
                cancellation = await asyncio.shield(cancellation_task)
            except asyncio.CancelledError:
                caller_cancelled = True
                try:
                    cancellation = await cancellation_task
                except Exception as exc:  # noqa: BLE001
                    cancellation_error = exc
            except Exception as exc:  # noqa: BLE001
                cancellation_error = exc

            if cancellation_error is not None:
                logger.warning(
                    "Shell Job 取消请求失败: "
                    f"job_id={job_id}, error={type(cancellation_error).__name__}"
                )
                with self._lock:
                    record.error = (
                        str(cancellation_error).strip()
                        or type(cancellation_error).__name__
                    )
                    record.cancel_resolved.set()
                    result = self._result(record, include_output=False)
                if caller_cancelled:
                    raise asyncio.CancelledError
                return result

            assert cancellation is not None
            with self._lock:
                if cancellation.signal_sent:
                    record.cancel_confirmed = True
                record.cancel_resolved.set()
                is_terminal = record.status in _TERMINAL_STATUSES
            if cancellation.exited and not is_terminal:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(record.done.wait()),
                        timeout=2.0,
                    )
            if caller_cancelled:
                raise asyncio.CancelledError
        with self._lock:
            if record.status in _TERMINAL_STATUSES and review_terminal:
                record.reviewed_at = datetime.now(UTC)
            return self._result(record, include_output=False)

    async def cancel(self, job_id: str) -> ShellJobResult | ShellJobError:
        """取消任务并在已确认终态时标记为已查看"""
        return await self._cancel(job_id, review_terminal=True)

    def model_context(self) -> dict[str, list[dict[str, object]]]:
        """构造不含命令正文和日志正文的模型临时状态"""
        running: list[dict[str, object]] = []
        finished_unreviewed: list[dict[str, object]] = []
        with self._lock:
            for record in sorted(
                self._records.values(),
                key=lambda item: item.started_at,
            ):
                common: dict[str, object] = {
                    "job_id": record.job_id,
                    "status": record.status,
                    "output_path": record.output_path,
                }
                if record.status not in _TERMINAL_STATUSES:
                    running.append(
                        {
                            **common,
                            "started_at": record.started_at.isoformat(),
                            "elapsed_seconds": _elapsed_seconds(record),
                        }
                    )
                elif record.reviewed_at is None:
                    finished_unreviewed.append(
                        {
                            **common,
                            "exit_code": record.exit_code,
                            "finished_at": (
                                record.finished_at.isoformat()
                                if record.finished_at is not None
                                else None
                            ),
                            "output_truncated": record.output_truncated,
                        }
                    )
        return {
            "running": running,
            "finished_unreviewed": finished_unreviewed,
        }

    async def cleanup(self) -> None:
        """在 Agent Run 结束前取消任务、等待监控并清空 Registry"""
        async with self._cleanup_lock:
            with self._lock:
                if self._closing and not self._records:
                    return
                self._closing = True
                active_ids = [
                    record.job_id
                    for record in self._records.values()
                    if record.status not in _TERMINAL_STATUSES
                ]

            deadline = asyncio.get_running_loop().time() + _CLEANUP_WAIT_SECONDS
            while active_ids and asyncio.get_running_loop().time() < deadline:
                await asyncio.gather(
                    *(
                        self._cancel(job_id, review_terminal=False)
                        for job_id in active_ids
                    ),
                    return_exceptions=True,
                )
                await asyncio.sleep(0.05)
                with self._lock:
                    active_ids = [
                        record.job_id
                        for record in self._records.values()
                        if record.status not in _TERMINAL_STATUSES
                    ]

            with self._lock:
                for job_id in active_ids:
                    record = self._records[job_id]
                    record.status = "interrupted"
                    record.finished_at = datetime.now(UTC)
                    record.error = "Agent Run 结束时无法确认 Shell Job 已退出"
                    record.done.set()
                records = list(self._records.values())
                monitor_tasks = [
                    record.monitor_task
                    for record in records
                    if record.monitor_task is not None
                ]

            if monitor_tasks:
                done, pending = await asyncio.wait(monitor_tasks, timeout=1.0)
                del done
                for task in pending:
                    task.cancel()
            await asyncio.gather(
                *(
                    self._backend.acleanup_shell_job_control(record.job_id)
                    for record in records
                ),
                return_exceptions=True,
            )
            with self._lock:
                self._records.clear()


def _append_shell_context(
    request: ModelRequest[Any],
    context: dict[str, list[dict[str, object]]],
) -> ModelRequest[Any]:
    """只在请求副本的系统消息中附加 Shell Job 状态"""
    if not context["running"] and not context["finished_unreviewed"]:
        return request
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    block = f"<shell_jobs>\n{payload}\n</shell_jobs>"
    current = request.system_message
    if current is None:
        system_message = SystemMessage(content=block)
    elif isinstance(current.content, str):
        system_message = current.model_copy(
            update={"content": f"{current.content}\n\n{block}"}
        )
    else:
        content = [*current.content, {"type": "text", "text": f"\n\n{block}"}]
        system_message = current.model_copy(update={"content": cast(Any, content)})
    return request.override(system_message=system_message)


class ShellJobContextMiddleware(AgentMiddleware[Any, Any, Any]):
    """在单次模型请求副本中附加当前 Shell Job 状态"""

    def __init__(self, runtime: ShellJobRuntime) -> None:
        """绑定与四个 Shell 工具共享的 Run 级 Runtime"""
        self._runtime = runtime

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """投影同步模型请求"""
        return handler(_append_shell_context(request, self._runtime.model_context()))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """投影异步模型请求"""
        return await handler(
            _append_shell_context(request, self._runtime.model_context())
        )
