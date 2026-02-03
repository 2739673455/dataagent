import json
import sys
from pathlib import Path

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
from loguru import logger

LOG_DIR = Path(__file__).parent.parent / "logs"

# 控制台日志格式
LOG_FORMAT_CONSOLE = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:^8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def _format_json(record) -> bool:
    """格式化为 JSON"""
    log_obj = {
        "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": record["level"].name,
        "request_id": request_id_ctx.get(),
        "trace_id": trace_id_ctx.get(),
        "client_ip": client_ip_ctx.get(),
        "method": method_ctx.get(),
        "path": path_ctx.get(),
        "user_id": user_id_ctx.get(),
        "status": status_ctx.get("status"),
        "response_time_ms": response_time_ms_ctx.get(),
        "message": record["message"],
    }

    log_obj = {k: v for k, v in log_obj.items() if v}

    # 修改 record 的 message 字段
    record["message"] = json.dumps(log_obj, ensure_ascii=False)
    return True


def _setup_logger(cfg: LogCfg, filter_func):
    """配置日志输出"""
    # 控制台输出
    if cfg.to_console:
        logger.add(
            sink=sys.stdout,
            level=cfg.to_console_level,
            format=LOG_FORMAT_CONSOLE,
            colorize=True,
            catch=False,
            filter=filter_func,
            enqueue=True,
        )

    # 文件输出（JSON 格式）
    if cfg.to_file:
        (LOG_DIR / cfg.log_dir).mkdir(parents=True, exist_ok=True)
        logger.add(
            sink=str(LOG_DIR / cfg.log_dir / "{time:YYYY-MM-DD}.jsonl"),
            level=cfg.to_file_level,
            format="{message}",
            rotation=cfg.max_file_size,
            encoding="utf-8",
            catch=False,
            filter=lambda r: filter_func(r) and _format_json(r),
            enqueue=True,
        )


logger_configured = False

app_logger = logger.bind(tag="app")
auth_logger = logger.bind(tag="auth")


def setup_logger():
    """初始化日志配置"""
    global logger_configured
    if not logger_configured:
        logger.remove()  # 移除默认的日志输出
        _setup_logger(CFG.log.app, lambda record: record["extra"].get("tag") == "app")
        _setup_logger(CFG.log.auth, lambda record: record["extra"].get("tag") == "auth")
        logger_configured = True
