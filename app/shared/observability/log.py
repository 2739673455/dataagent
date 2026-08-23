from __future__ import annotations

import json
import sys
import traceback
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.shared.config.app_config import cfg
from app.shared.observability import context

if TYPE_CHECKING:
    from loguru import Record

LOG_DIR = Path(__file__).parents[3] / "logs"
_JSON_LINE_KEY = "_json_line"


def _build_log_payload(record: Record) -> dict[str, Any]:
    """构造结构化日志载荷"""
    name = record.get("name") or ""
    function = record.get("function") or ""
    line = record.get("line") or ""
    location = f"{name}:{function}:{line}" if name or function or line else ""

    payload = {
        "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": record["level"].name,
        "location": location,
        "method": context.method_ctx.get(),
        "path": context.path_ctx.get(),
        "user_id": context.user_id_ctx.get(),
        "message": record["message"],
        "request_id": context.request_id_ctx.get(),
        "trace_id": context.trace_id_ctx.get(),
        "client_ip": context.client_ip_ctx.get(),
    }
    payload.update(
        {
            key: value
            for key, value in record["extra"].items()
            if key != _JSON_LINE_KEY and key not in payload
        }
    )

    exc_info = record.get("exception")
    if exc_info and exc_info.value is not None:
        payload["exception"] = "".join(
            traceback.format_exception(
                exc_info.type,
                exc_info.value,
                exc_info.traceback,
            )
        )

    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != ""
    }


def _json_formatter(record: Record) -> str:
    """序列化单行 JSON 且不追加 Loguru 异常文本"""
    record["extra"][_JSON_LINE_KEY] = json.dumps(
        _build_log_payload(record),
        ensure_ascii=False,
        default=str,
    )
    return f"{{extra[{_JSON_LINE_KEY}]}}\n"


def _console_formatter(record: Record) -> str:
    """按白名单渲染控制台上下文与原生异常"""
    template = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level:^8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    extra = record["extra"]

    context_fields = [
        f"{key}={{extra[{key}]}}"
        for key in ("status", "problem_type", "exc_type")
        if extra.get(key) is not None and extra.get(key) != ""
    ]
    if context_fields:
        template += "\n  <cyan>context:</cyan> " + " ".join(context_fields)
    if extra.get("detail") is not None and extra.get("detail") != "":
        template += "\n  <yellow>detail:</yellow> <level>{extra[detail]}</level>"

    return template + "\n{exception}"


@cache
def setup_logger() -> None:
    """初始化日志配置"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.configure(
        handlers=[
            {
                "sink": sys.stdout,
                "level": cfg.log.level,
                "format": _console_formatter,
                "colorize": True,
                "backtrace": False,
                "diagnose": False,
                "catch": True,
                "enqueue": True,
            },
            {
                "sink": str(LOG_DIR / "{time:YYYY-MM-DD}.jsonl"),
                "level": cfg.log.level,
                "format": _json_formatter,
                "rotation": cfg.log.rotation,
                "encoding": "utf-8",
                "backtrace": False,
                "diagnose": False,
                "catch": True,
                "enqueue": True,
            },
        ],
    )
