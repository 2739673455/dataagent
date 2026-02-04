import logging
import sys
from datetime import datetime
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

LOGGER_CONFIGURED = False  # 日志是否已初始化
LOG_DIR = Path(__file__).parent.parent / "logs"  # 日志目录


class DateSizeRotatingFileHandler(logging.Handler):
    """同时按日期与文件大小滚动的日志处理器"""

    terminator = "\n"

    def __init__(self, log_dir: Path, max_bytes: int, encoding: str = "utf-8"):
        super().__init__()
        self.log_dir = log_dir  # 日志输出目录
        self.max_bytes = max_bytes  # 单个文件最大大小（字节）
        self.encoding = encoding  # 输出编码
        self.current_date = None  # 当前日期（同日内按大小滚动）
        self.sequence = 0  # 同日日志序号
        self.stream = None  # 当前文件流
        self._open()

    def _get_date_str(self) -> str:
        """获取当前日期字符串"""
        return datetime.now().strftime("%Y-%m-%d")

    def _get_log_path(self) -> Path:
        """获取当前日志路径"""
        # 文件名：YYYY-MM-DD-sequence.jsonl
        return self.log_dir / f"{self.current_date}-{self.sequence}.jsonl"

    def _open(self) -> None:
        """打开流"""
        # 确保目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if self.current_date is None:
            self.current_date = self._get_date_str()
        # 打开当前日志文件
        path = self._get_log_path()
        self.stream = open(path, "a", encoding=self.encoding)

    def _should_rollover(self, record: logging.LogRecord) -> bool:
        """判断是否需要滚动日志"""
        # 日期变更直接滚动
        if self.current_date != self._get_date_str():
            return True
        # 未设置大小限制则不滚动
        if self.max_bytes <= 0:
            return False
        # 流未打开时强制滚动
        if self.stream is None:
            return True
        # 写入后超限则滚动
        msg = self.format(record)
        msg_bytes = (msg + self.terminator).encode(self.encoding)
        self.stream.flush()
        return self.stream.tell() + len(msg_bytes) > self.max_bytes

    def _do_rollover(self) -> None:
        """执行日志滚动"""
        if self.stream:
            self.stream.close()  # 关闭旧文件
        if self.current_date != self._get_date_str():  # 如果日期发生变化
            self.sequence = 0  # 重置序号
            self.current_date = self._get_date_str()  # 更新日期
        else:  # 如果日期没变
            self.sequence += 1  # 序号递增
        self._open()  # 打开新文件

    def emit(self, record: logging.LogRecord) -> None:
        """输出日志并处理滚动"""
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
        """关闭流并清理"""
        if self.stream:
            self.stream.close()
            self.stream = None
        super().close()


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
    units = {
        "GB": 1024**3,
        "MB": 1024**2,
        "KB": 1024,
        "G": 1024**3,
        "M": 1024**2,
        "K": 1024,
        "B": 1,
    }
    for unit, multiplier in units.items():
        if raw.endswith(unit):
            number = raw[: -len(unit)]
            if number:
                return int(float(number) * multiplier)
    # 无法解析时报错
    raise ValueError(f"Invalid size value: {value}")


def _json_renderer(logger, method_name, event_dict):
    """将 structlog 的 event 字段转为 message，并输出 JSON"""
    # structlog 默认使用 event 作为主消息字段
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    # 输出为 JSON 字符串
    return structlog.processors.JSONRenderer(ensure_ascii=False)(
        logger, method_name, event_dict
    )


def _build_logger(logger_name: str) -> logging.Logger:
    """获取并初始化 stdlib logger"""
    # 获取 stdlib logger 实例
    logger = logging.getLogger(logger_name)
    # 清空历史 handler，避免重复输出
    logger.handlers.clear()
    # 日志级别统一设置为 INFO，再由 handler 自己控制级别
    logger.setLevel(logging.INFO)
    # 禁止向 root 传播，避免被根 logger 再次输出
    logger.propagate = False
    return logger


def _make_console_handler(cfg: LogCfg, processors: list) -> logging.Handler:
    """创建控制台日志处理器"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(cfg.to_console_level)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=processors,
        )
    )
    return handler


def _make_file_handler(cfg: LogCfg, processors: list) -> logging.Handler:
    """创建文件日志处理器（JSONL）"""
    handler = DateSizeRotatingFileHandler(
        log_dir=LOG_DIR / cfg.log_dir,
        max_bytes=_parse_size(cfg.max_file_size),
        encoding="utf-8",
    )
    handler.setLevel(cfg.to_file_level)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=_json_renderer,
            foreign_pre_chain=processors,
        )
    )
    return handler


def _setup_logger(cfg: LogCfg, logger_name: str, processors: list):
    """配置并挂载 structlog 的输出处理器"""

    logger = _build_logger(logger_name)

    if cfg.to_console:
        logger.addHandler(_make_console_handler(cfg, processors))

    if cfg.to_file:
        logger.addHandler(_make_file_handler(cfg, processors))


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
    """删除空值字段"""
    for key in list(event_dict.keys()):
        if key == "event":
            continue
        value = event_dict.get(key)
        if value is None or value == "":
            event_dict.pop(key, None)
    return event_dict


def setup_logger():
    """初始化日志配置"""

    global LOGGER_CONFIGURED
    if LOGGER_CONFIGURED:  # 避免重复初始化
        return

    # 处理器链：时间、等级、logger 名称、上下文、异常信息等
    processors = [
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_context_fields,
        _drop_empty_fields,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # structlog 与 stdlib logging 的桥接配置
    structlog.configure(
        processors=processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 应用配置
    _setup_logger(CFG.log, "auth", processors)
    LOGGER_CONFIGURED = True


logger = structlog.get_logger("auth")
