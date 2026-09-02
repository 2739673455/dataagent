"""Agent Shell Job 运行时。"""

from __future__ import annotations

import asyncio
import contextlib
import math
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict

from app.sandbox.shell_runner import (
    DockerShellJobRunner,
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
SHELL_JOB_MAX_STATUS_WAIT_SECONDS: Final = 60.0
_CLEANUP_WAIT_SECONDS = 8.0


class ShellJobResult(BaseModel):
    """已脱离前台的 Shell Job 当前或最终状态。"""

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
    error: str | None = None


class ShellJobSummary(BaseModel):
    """已脱离前台的 Shell Job 列表项。"""

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


class ShellJobError(BaseModel):
    """Shell Job 工具的稳定错误结构。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["error"] = "error"
    code: Literal["job_not_found", "invalid_request"]
    message: str


@dataclass(slots=True)
class _ShellJobRecord:
    """当前 Agent Run 内部保存的单个 Shell 命令执行记录。"""

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
    error: str | None = None
    detached: bool = False
    consumed: bool = False
    cancel_requested: bool = False
    cancel_confirmed: bool = False
    cancel_resolved: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    cancel_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    monitor_task: asyncio.Task[None] | None = field(default=None, repr=False)


def _elapsed_seconds(record: _ShellJobRecord, now: datetime | None = None) -> float:
    """计算任务从实际启动到当前或终态的耗时。"""
    endpoint = record.finished_at or now or datetime.now(UTC)
    return round(max(0.0, (endpoint - record.started_at).total_seconds()), 3)


class ShellJobRuntime:
    """协调当前 Agent 运行边界内独占的 Shell 命令与后台任务。"""

    def __init__(
        self,
        executor: DockerShellJobRunner,
        *,
        foreground_wait_seconds: float = _FOREGROUND_WAIT_SECONDS,
    ) -> None:
        """绑定 Session Sandbox，并配置仅供测试缩短的前台等待时间。"""
        if not math.isfinite(foreground_wait_seconds) or foreground_wait_seconds < 0:
            raise ValueError("foreground_wait_seconds 必须是非负有限数")
        self._executor = executor
        self._workspace_dir = executor.workspace_dir.rstrip("/")
        self._foreground_wait_seconds = foreground_wait_seconds
        self._records: dict[str, _ShellJobRecord] = {}
        self._lock = threading.RLock()
        self._cleanup_lock = asyncio.Lock()
        self._closing = False

    def _new_job_id(self) -> str:
        """生成当前 Runtime 内唯一的内部 Shell Job 标识。"""
        with self._lock:
            while True:
                job_id = f"job_{secrets.token_hex(4)}"
                if job_id not in self._records:
                    return job_id

    @staticmethod
    def _result(record: _ShellJobRecord) -> ShellJobResult:
        """从内部记录生成已脱离前台任务的公开状态。"""
        return ShellJobResult(
            job_id=record.job_id,
            status=record.status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            elapsed_seconds=_elapsed_seconds(record),
            exit_code=record.exit_code,
            output_path=record.output_path,
            output_truncated=record.output_truncated,
            output_inline_truncated=record.output_inline_truncated,
            error=record.error,
        )

    @staticmethod
    def _summary(record: _ShellJobRecord) -> ShellJobSummary:
        """从内部记录生成后台任务列表摘要。"""
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
        )

    @staticmethod
    def _foreground_output(record: _ShellJobRecord) -> str:
        """构造前台命令仅包含实际结果的字符串返回值。"""
        if record.output is not None:
            output = record.output
        elif record.error is not None:
            output = record.error
        elif record.exit_code not in (None, 0):
            output = f"Shell 命令以退出码 {record.exit_code} 结束"
        elif record.status != "completed":
            output = f"Shell 命令未完成: {record.status}"
        else:
            output = ""
        if not record.output_inline_truncated:
            return output
        separator = "" if not output or output.endswith("\n") else "\n"
        return f"{output}{separator}详细输出文件: {record.output_path}"

    def _available_record(self, job_id: str) -> _ShellJobRecord | None:
        """返回可由模型管理的后台任务；调用方必须持有 Registry 锁。"""
        record = self._records.get(job_id)
        if record is None or not record.detached or record.consumed:
            return None
        return record

    async def _monitor(self, record: _ShellJobRecord) -> None:
        """持有独立监控任务并把 Backend 结果提交为单一终态。"""
        loop = asyncio.get_running_loop()

        def started_callback() -> None:
            """把监控线程观察到的实际启动事件投递回事件循环。"""
            loop.call_soon_threadsafe(self._mark_started, record)

        try:
            execution = await self._executor.arun(
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
            record.error = execution.error
            record.done.set()

    def _mark_started(self, record: _ShellJobRecord) -> None:
        """在事件循环线程提交 Backend 已实际启动命令的状态。"""
        with self._lock:
            if record.started.is_set() or record.status in _TERMINAL_STATUSES:
                return
            record.started_at = datetime.now(UTC)
            record.started.set()

    @staticmethod
    async def _wait_until_started_or_done(record: _ShellJobRecord) -> None:
        """等待命令实际启动；启动失败时由终态事件提前结束等待。"""
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

    async def _discard_foreground(self, record: _ShellJobRecord) -> None:
        """清理不公开的前台命令临时文件和内部记录。"""
        try:
            await self._executor.acleanup(
                record.job_id,
                remove_log=not record.output_inline_truncated,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "清理前台 Shell 命令临时文件失败: "
                f"job_id={record.job_id}, error={type(exc).__name__}"
            )
        finally:
            with self._lock:
                if self._records.get(record.job_id) is record:
                    self._records.pop(record.job_id)

    async def start(self, command: str) -> str | ShellJobResult:
        """启动命令，前台结束返回输出，超时后发布后台任务。"""
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
                output_path=(
                    f"{self._workspace_dir}/large_tool_results/shell_jobs/{job_id}.log"
                ),
                done=asyncio.Event(),
            )
            self._records[job_id] = record
            record.monitor_task = asyncio.create_task(
                self._monitor(record),
                name=f"shell-job-{job_id}",
            )

        try:
            await self._wait_until_started_or_done(record)
            if not record.done.is_set():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(record.done.wait()),
                        timeout=self._foreground_wait_seconds,
                    )
                except TimeoutError:
                    with self._lock:
                        if not record.done.is_set():
                            record.detached = True
                            return self._result(record)

            with self._lock:
                foreground_output = self._foreground_output(record)
            await self._discard_foreground(record)
            return foreground_output
        except asyncio.CancelledError:
            with self._lock:
                if not record.done.is_set():
                    record.detached = True
            raise

    def list(self) -> list[ShellJobSummary]:
        """列出尚未消费的后台任务，不消费任何任务。"""
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.started_at)
            return [
                self._summary(record)
                for record in records
                if record.detached and not record.consumed
            ]

    def _not_found(self, job_id: str) -> ShellJobError:
        """构造不泄露其他 Run 信息的未找到结果。"""
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
        """查看后台任务；终态结果只允许一个调用方消费。"""
        if (
            not math.isfinite(wait_seconds)
            or wait_seconds < 0
            or wait_seconds > SHELL_JOB_MAX_STATUS_WAIT_SECONDS
        ):
            raise ValueError("wait_seconds 必须是 0 到 60 之间的有限数")
        with self._lock:
            record = self._available_record(job_id)
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
            if self._available_record(job_id) is not record:
                return self._not_found(job_id)
            if record.status in _TERMINAL_STATUSES:
                record.consumed = True
            return self._result(record)

    async def _cancel(
        self,
        job_id: str,
        *,
        consume_terminal: bool,
        require_detached: bool,
    ) -> ShellJobResult | ShellJobError:
        """串行提交一次进程组取消请求并处理自然结束竞态。"""
        with self._lock:
            record = (
                self._available_record(job_id)
                if require_detached
                else self._records.get(job_id)
            )
            if record is None:
                return self._not_found(job_id)
        async with record.cancel_lock:
            with self._lock:
                available = (
                    self._available_record(job_id)
                    if require_detached
                    else self._records.get(job_id)
                )
                if available is not record:
                    return self._not_found(job_id)
                if record.status in _TERMINAL_STATUSES:
                    if consume_terminal:
                        record.consumed = True
                    return self._result(record)
                record.cancel_requested = True
                record.cancel_resolved.clear()
                record.status = "cancelling"

            cancellation_task = asyncio.create_task(self._executor.acancel(job_id))
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
                    result = self._result(record)
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
            if require_detached and self._available_record(job_id) is not record:
                return self._not_found(job_id)
            if record.status in _TERMINAL_STATUSES and consume_terminal:
                record.consumed = True
            return self._result(record)

    async def cancel(self, job_id: str) -> ShellJobResult | ShellJobError:
        """取消后台任务；返回终态时消费该任务。"""
        return await self._cancel(
            job_id,
            consume_terminal=True,
            require_detached=True,
        )

    async def cleanup(self) -> None:
        """在 Agent Run 结束前取消任务、等待监控并清空内部记录。"""
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
                        self._cancel(
                            job_id,
                            consume_terminal=False,
                            require_detached=False,
                        )
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
                _, pending = await asyncio.wait(monitor_tasks, timeout=1.0)
                for task in pending:
                    task.cancel()
            await asyncio.gather(
                *(
                    self._executor.acleanup(
                        record.job_id,
                        remove_log=not record.detached,
                    )
                    for record in records
                ),
                return_exceptions=True,
            )
            with self._lock:
                self._records.clear()
