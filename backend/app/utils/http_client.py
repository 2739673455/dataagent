import httpx

# 异步客户端
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """获取全局异步客户端"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30.0,
            ),
        )
    return _http_client


async def aget(url: str, **kwargs) -> httpx.Response:
    """异步 GET 请求"""
    async with get_http_client() as client:
        return await client.get(url, **kwargs)


async def apost(url: str, **kwargs) -> httpx.Response:
    """异步 POST 请求"""
    async with get_http_client() as client:
        return await client.post(url, **kwargs)


async def close_clients():
    """关闭所有客户端"""
    global _http_client, _sync_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
