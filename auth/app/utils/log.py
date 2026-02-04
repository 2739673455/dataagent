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

LOGGER_CONFIGURED = False  # 日志是否已初始化
LOG_DIR = Path(__file__).parent.parent / "logs"  # 日志文件目录


def _build_log_json(record):
    """格式化为 JSON"""
    log_json = {
        "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": record["level"].name,
        "request_id": request_id_ctx.get(),
        "trace_id": trace_id_ctx.get(),
        "client_ip": client_ip_ctx.get(),
        "method": method_ctx.get(),
        "path": path_ctx.get(),
        "user_id": user_id_ctx.get(),
        "status": status_ctx.get(),
        "response_time_ms": response_time_ms_ctx.get(),
        "message": record["message"],
    }

    # 将 extra 中的信息添加到输出
    extra = {k: v for k, v in record.get("extra", {}).items() if k != "json"}
    log_json.update(extra)

    log_json = {k: v for k, v in log_json.items() if v}  # 滤空
    record["extra"]["json"] = json.dumps(log_json, ensure_ascii=False)


def _setup_console_logger(cfg: LogCfg):
    """配置控制台日志输出"""
    logger.add(
        sink=sys.stdout,
        level=cfg.to_console_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level:^8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
        catch=True,
        enqueue=True,
    )


def _setup_file_logger(cfg: LogCfg):
    """配置文件日志输出（JSON 格式）"""
    (LOG_DIR / cfg.log_dir).mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=str(LOG_DIR / cfg.log_dir / "{time:YYYY-MM-DD}.jsonl"),
        level=cfg.to_file_level,
        format="{extra[json]}",
        rotation=cfg.max_file_size,
        encoding="utf-8",
        catch=True,
        enqueue=True,
    )


def _setup_logger(cfg: LogCfg):
    """配置日志输出"""
    if cfg.to_console:
        _setup_console_logger(cfg)
    if cfg.to_file:
        _setup_file_logger(cfg)


def setup_logger():
    """初始化日志配置"""
    global LOGGER_CONFIGURED
    if not LOGGER_CONFIGURED:
        logger.remove()  # 移除默认的日志输出
        logger.configure(patcher=_build_log_json)
        _setup_logger(CFG.log)
        LOGGER_CONFIGURED = True
