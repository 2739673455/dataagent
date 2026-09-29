"""Docker exec 输出流清理。"""

import errno


def close_exec_stream(stream: object) -> None:
    """关闭 Docker exec 流及其底层 HTTP 响应。"""
    close_stream = getattr(stream, "close", None)
    try:
        if callable(close_stream):
            close_stream()
    except OSError as exc:
        # 输出读完后 socket 可能已断开，无需再次 shutdown。
        if exc.errno != errno.ENOTCONN:
            raise
    finally:
        # 即使流关闭失败，也释放底层 HTTP 响应。
        response = getattr(stream, "_response", None)
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()
