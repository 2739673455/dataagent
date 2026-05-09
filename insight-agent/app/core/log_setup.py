import json
import sys
import traceback
from pathlib import Path

from loguru import logger

from app.core import context
from app.core.settings import LogCfg, cfg

# 路径常量
CURRENT_DIR = Path(__file__).parent
ROOT_DIR = CURRENT_DIR.parent.parent  # 项目根目录

LOGGER_CONFIGURED = False  # 日志是否已初始化
LOG_DIR = ROOT_DIR / "logs"  # 日志文件目录


def _build_log_json(record):
    """格式化为 JSON"""
    log_json = {
        "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": record["level"].name,
        "method": context.method_ctx.get(),
        "path": context.path_ctx.get(),
        "user_id": context.user_id_ctx.get(),
        "message": record["message"],
        "request_id": context.request_id_ctx.get(),
        "trace_id": context.trace_id_ctx.get(),
        "client_ip": context.client_ip_ctx.get(),
    }

    # 将 extra 中的信息添加到输出
    extra = {k: v for k, v in record.get("extra", {}).items() if k != "json"}
    log_json.update(extra)

    # 捕获异常信息（如果有），格式化为字符串
    exc_info = record.get("exception")
    if exc_info:
        if exc_info.value is not None:
            log_json["exception"] = "".join(
                traceback.format_exception(exc_info.type, exc_info.value, exc_info.traceback)
            )
        # 清除 exception，阻止 loguru 默认格式化
        record["exception"] = None

    log_json = {k: v for k, v in log_json.items() if v}  # 滤空
    record["extra"]["json"] = json.dumps(log_json, ensure_ascii=False)


def _setup_console_logger(cfg: LogCfg):
    """配置控制台日志输出"""
    logger.add(
        sink=sys.stdout,
        level=cfg.level,
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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=str(LOG_DIR / "{time:YYYY-MM-DD}.jsonl"),
        level=cfg.level,
        format="{extra[json]}",
        rotation=cfg.max_file_size,
        encoding="utf-8",
        catch=True,
        enqueue=True,
    )


def _setup_logger(cfg: LogCfg):
    """配置日志输出"""
    _setup_console_logger(cfg)
    _setup_file_logger(cfg)


def setup_logger():
    """初始化日志配置"""
    global LOGGER_CONFIGURED
    if not LOGGER_CONFIGURED:
        logger.remove()  # 移除默认的日志输出
        logger.configure(patcher=_build_log_json)
        _setup_logger(cfg.log)
        LOGGER_CONFIGURED = True
