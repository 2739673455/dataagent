from redis.asyncio import Redis

from app.core.settings import cfg

_redis_client: Redis | None = None


async def get() -> Redis:
    """获取 Redis 客户端单例，断连时自动重建"""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.ping()  # pyright: ignore[reportGeneralTypeIssues]
            return _redis_client
        except Exception:
            _redis_client = None

    _redis_client = Redis(
        host=cfg.redis.host,
        port=cfg.redis.port,
        password=cfg.redis.password or None,
        db=cfg.redis.db,
        decode_responses=True,
        retry_on_timeout=True,
        health_check_interval=30,
        socket_connect_timeout=10,
        socket_keepalive=True,
    )
    return _redis_client


async def close_redis() -> None:
    """关闭 Redis 连接"""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
