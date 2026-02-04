import logging
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import structlog
from app.config import CFG, LogCfg
from app.utils.context import (
    client_ip_ctx,
    method_ctx,
    path_ctx,
    request_id_ctx,
    response_time_ms_ctx,
    status_ctx,
    trace_id_ctx,
    user_id_ctx,
)

_LOGGER_CONFIGURED = False
LOG_DIR = Path(str(files("app"))).parent / "logs"  # 日志目录


def _parse_size(value: Any) -> int:
    """解析文件大小字符串"""
    # 允许直接传入数值类型
    if isinstance(value, (int, float)):
        return int(value)
    # 统一格式，便于解析如 "10MB"
    raw = str(value).strip().upper().replace(" ", "")
    # 纯数字直接转换
    if raw.isdigit():
        return int(raw)
    # 识别带单位的字符串
    units = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    for unit, multiplier in units.items():
        if raw.endswith(unit):
            number = raw[: -len(unit)]
            if number:
                return int(float(number) * multiplier)
    # 无法解析时报错
    raise ValueError(f"Invalid size value: {value}")


def _add_context_fields(logger, method_name, event_dict):
    """添加上下文字段"""
    event_dict.update(
        {
            "request_id": request_id_ctx.get(),
            "trace_id": trace_id_ctx.get(),
            "client_ip": client_ip_ctx.get(),
            "method": method_ctx.get(),
            "path": path_ctx.get(),
            "user_id": user_id_ctx.get(),
            "status": status_ctx.get(),
            "response_time_ms": response_time_ms_ctx.get(),
        }
    )
    return event_dict


def _drop_empty_fields(logger, method_name, event_dict):
    """删除空的字段"""
    for key in list(event_dict.keys()):
        if key == "event":
            continue
        value = event_dict.get(key)
        if value is None or value == "":
            event_dict.pop(key, None)
    return event_dict


def _json_renderer(logger, method_name, event_dict):
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return structlog.processors.JSONRenderer(ensure_ascii=False)(
        logger, method_name, event_dict
    )


class DateSizeRotatingFileHandler(logging.Handler):
    terminator = "\n"

    def __init__(self, log_dir: Path, max_bytes: int, encoding: str = "utf-8"):
        super().__init__()
        self.log_dir = log_dir
        self.max_bytes = max_bytes
        self.encoding = encoding
        self.current_date = None
        self.sequence = 0
        self.stream = None
        self._open()

    def _get_date_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _get_log_path(self) -> Path:
        base = self.log_dir / f"{self.current_date}.jsonl"
        if self.sequence == 0:
            return base
        return self.log_dir / f"{self.current_date}.jsonl.{self.sequence}"

    def _open(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.current_date is None:
            self.current_date = self._get_date_str()
        path = self._get_log_path()
        self.stream = open(path, "a", encoding=self.encoding)

    def _should_rollover(self, record: logging.LogRecord) -> bool:
        if self.current_date != self._get_date_str():
            return True
        if self.max_bytes <= 0:
            return False
        if self.stream is None:
            return True
        msg = self.format(record)
        msg_bytes = (msg + self.terminator).encode(self.encoding)
        self.stream.flush()
        return self.stream.tell() + len(msg_bytes) > self.max_bytes

    def _do_rollover(self) -> None:
        if self.stream:
            self.stream.close()
        if self.current_date != self._get_date_str():
            self.sequence = 0
            self.current_date = self._get_date_str()
        else:
            self.sequence += 1
        self._open()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._should_rollover(record):
                self._do_rollover()
            msg = self.format(record)
            assert self.stream is not None, "stream is None"
            self.stream.write(msg + self.terminator)
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        super().close()


app_logger = structlog.get_logger("app")
auth_logger = structlog.get_logger("auth")


def _get_log_cfg(log_cfg: Any, name: str) -> LogCfg:
    return getattr(log_cfg, name, log_cfg)


def _setup_logger(cfg: LogCfg, logger_name: str, processors: list):
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if cfg.to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(cfg.to_console_level)
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(colors=True),
                foreign_pre_chain=processors,
            )
        )
        logger.addHandler(console_handler)

    if cfg.to_file:
        log_dir = LOG_DIR / cfg.log_dir
        file_handler = DateSizeRotatingFileHandler(
            log_dir=log_dir,
            max_bytes=_parse_size(cfg.max_file_size),
            encoding="utf-8",
        )
        file_handler.setLevel(cfg.to_file_level)
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=_json_renderer,
                foreign_pre_chain=processors,
            )
        )
        logger.addHandler(file_handler)


def setup_logger():
    """初始化日志配置"""
    global _LOGGER_CONFIGURED
    if _LOGGER_CONFIGURED:
        return

    processors = [
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_context_fields,
        _drop_empty_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    log_cfg = CFG.log
    _setup_logger(_get_log_cfg(log_cfg, "app"), "app", processors)
    _setup_logger(_get_log_cfg(log_cfg, "auth"), "auth", processors)
    _LOGGER_CONFIGURED = True


if __name__ == "__main__":
    setup_logger()
