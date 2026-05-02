import threading

import httpx

_http_client: httpx.AsyncClient | None = None
_client_lock = threading.Lock()


def get_http_client() -> httpx.AsyncClient:
    """获取全局异步客户端"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        with _client_lock:
            if _http_client is None or _http_client.is_closed:
                _http_client = httpx.AsyncClient(
                    timeout=300.0,
                    limits=httpx.Limits(
                        max_keepalive_connections=20,
                        max_connections=100,
                        keepalive_expiry=30.0,
                    ),
                )
    return _http_client


async def close_http_client():
    """关闭客户端"""
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
