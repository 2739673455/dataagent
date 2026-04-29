from datetime import datetime, timedelta, timezone

BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def now_str() -> str:
    """当前时间字符串"""
    return datetime.now(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def future_str(seconds: int) -> str:
    """未来时间字符串"""
    return (datetime.now(BEIJING_TIMEZONE) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
