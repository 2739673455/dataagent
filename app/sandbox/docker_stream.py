"""Docker exec 输出流清理。"""

import errno


def close_exec_stream(stream: object) -> None:
    """关闭输出流，并保证底层 HTTP 响应释放。"""
    try:
        close_stream = getattr(stream, "close", None)
        if callable(close_stream):
            try:
                close_stream()
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    raise
    finally:
        response = getattr(stream, "_response", None)
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()
