"""WebSocket 临时令牌数据访问"""

from pydantic import BaseModel

from app.core import redis as redis_util

# Redis Key
WEBSOCKET_TOKEN_KEY = "ws_token:{token}"


class WebSocketTokenData(BaseModel):
    """WebSocket 临时令牌数据模型"""

    user_id: int


def _make_websocket_token_key(token: str) -> str:
    """构造 WebSocket token key

    Args:
        token: WebSocket 临时令牌

    Returns:
        Redis key，格式为 "ws_token:{token}"
    """
    return WEBSOCKET_TOKEN_KEY.format(token=token)


async def create(token: str, user_id: int, expire_seconds: int) -> None:
    """创建 WebSocket 临时令牌

    Args:
        token: WebSocket 临时令牌
        user_id: 用户 ID
        expire_seconds: 过期秒数
    """
    r = await redis_util.get()
    key = _make_websocket_token_key(token)
    data = WebSocketTokenData(user_id=user_id)
    await r.setex(key, expire_seconds, data.model_dump_json())


async def consume(token: str) -> WebSocketTokenData | None:
    """消费 WebSocket 临时令牌

    Args:
        token: WebSocket 临时令牌

    Returns:
        临时令牌数据；不存在、过期或已被消费则返回 None
    """
    r = await redis_util.get()
    key = _make_websocket_token_key(token)
    data = await r.getdel(key)
    if data is None:
        return None
    return WebSocketTokenData.model_validate_json(data)
