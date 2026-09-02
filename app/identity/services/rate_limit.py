"""认证接口的有界速率限制。"""

import asyncio
import hashlib
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from anyio import to_thread
from redis import Redis
from redis.exceptions import RedisError

from app.identity import errors as auth_error

LOGIN_IP_RATE_LIMIT = 30
LOGIN_IDENTIFIER_RATE_LIMIT = 10
LOGIN_RATE_WINDOW_SECONDS = 60
REFRESH_RATE_LIMIT = 60
REFRESH_RATE_WINDOW_SECONDS = 60
IP_RATE_LIMIT_MAX_KEYS = 10_000
IDENTIFIER_RATE_LIMIT_MAX_KEYS = 50_000

_REDIS_CONSUME_SCRIPT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local max_keys = tonumber(ARGV[4])
local digest = ARGV[5]
local cutoff = now - window

redis.call('zremrangebyscore', KEYS[2], '-inf', now)
local bucket_exists = redis.call('exists', KEYS[1]) == 1
if not bucket_exists then
    redis.call('zrem', KEYS[2], digest)
    if redis.call('zcard', KEYS[2]) >= max_keys then
        local oldest = redis.call('zrange', KEYS[2], 0, 0, 'WITHSCORES')
        local retry_after = math.max(1, math.ceil((tonumber(oldest[2]) - now) / 1000))
        return {2, retry_after}
    end
end

redis.call('zremrangebyscore', KEYS[1], '-inf', cutoff)
redis.call('zadd', KEYS[2], now + window, digest)
redis.call('pexpire', KEYS[2], window)

local timestamps = redis.call('zrange', KEYS[1], 0, -1, 'WITHSCORES')
if #timestamps / 2 >= limit then
    local retry_after = math.max(
        1,
        math.ceil((tonumber(timestamps[2]) + window - now) / 1000)
    )
    return {1, retry_after}
end

local sequence = redis.call('incr', KEYS[3])
redis.call('pexpire', KEYS[3], window)
redis.call('zadd', KEYS[1], now, tostring(now) .. ':' .. tostring(sequence))
redis.call('pexpire', KEYS[1], window)
return {0, 0}
"""


@dataclass(frozen=True)
class RateLimitRule:
    """固定时间窗口内的请求上限。"""

    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        """校验限流阈值和时间窗口。"""
        if self.limit <= 0:
            raise ValueError("限流阈值必须为正整数")
        if self.window_seconds <= 0:
            raise ValueError("限流窗口时间必须为正数")


@dataclass
class _RateBucket:
    """单个限流键的请求时间队列。"""

    timestamps: deque[float] = field(default_factory=deque)
    last_seen: float = 0


class BoundedRateLimiter:
    """按最近使用顺序限制键数量的滑动窗口限流器。"""

    def __init__(
        self,
        rule: RateLimitRule,
        *,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化限流规则、容量上限和时间源。"""
        if max_keys <= 0:
            raise ValueError("max_keys 必须为正整数")
        self._rule = rule
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, _RateBucket] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def tracked_keys(self) -> int:
        """返回当前跟踪的限流键数量。"""
        return len(self._buckets)

    async def consume(self, key: str) -> None:
        """消费一次请求额度。"""
        key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        async with self._lock:
            now = self._clock()
            cutoff = now - self._rule.window_seconds
            self._remove_expired_buckets(cutoff)
            bucket = self._buckets.get(key_digest)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    retry_after = self._capacity_retry_after(now)
                    raise auth_error.RateLimitExceededError(
                        retry_after_seconds=retry_after,
                        detail="限流计数槽位已耗尽，请稍后重试",
                    )
                bucket = _RateBucket(last_seen=now)
                self._buckets[key_digest] = bucket
            else:
                self._prune_timestamps(bucket, cutoff)
                bucket.last_seen = now
                self._buckets.move_to_end(key_digest)

            if len(bucket.timestamps) >= self._rule.limit:
                retry_after = max(
                    1,
                    math.ceil(bucket.timestamps[0] + self._rule.window_seconds - now),
                )
                raise auth_error.RateLimitExceededError(retry_after_seconds=retry_after)
            bucket.timestamps.append(now)

    def _remove_expired_buckets(self, cutoff: float) -> None:
        """从最近最少使用端清除过期键。"""
        while self._buckets:
            key, bucket = next(iter(self._buckets.items()))
            if bucket.last_seen > cutoff:
                break
            self._buckets.pop(key)

    @staticmethod
    def _prune_timestamps(bucket: _RateBucket, cutoff: float) -> None:
        """清除滑动窗口外的请求记录。"""
        while bucket.timestamps and bucket.timestamps[0] <= cutoff:
            bucket.timestamps.popleft()

    def _capacity_retry_after(self, now: float) -> int:
        """计算最早限流键自然过期前的等待时间。"""
        oldest = next(iter(self._buckets.values()))
        return max(
            1,
            math.ceil(oldest.last_seen + self._rule.window_seconds - now),
        )


class RedisBoundedRateLimiter:
    """使用 Redis 共享计数、容量和过期清理的滑动窗口限流器。"""

    def __init__(
        self,
        rule: RateLimitRule,
        *,
        max_keys: int,
        redis_url: str,
        bucket_name: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """初始化共享 Redis 键空间和限流规则。"""
        if max_keys <= 0:
            raise ValueError("max_keys 必须为正整数")
        self._rule = rule
        self._max_keys = max_keys
        self._clock = clock
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = f"dataagent:identity:rate-limit:{bucket_name}"

    async def consume(self, key: str) -> None:
        """原子消费跨进程共享额度，Redis 只接收不可逆键摘要。"""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        now_milliseconds = int(self._clock() * 1000)
        window_milliseconds = max(1, int(self._rule.window_seconds * 1000))
        try:
            result = await to_thread.run_sync(
                self._consume_sync,
                digest,
                now_milliseconds,
                window_milliseconds,
            )
        except RedisError as exc:
            raise RuntimeError("认证限流 Redis 不可用") from exc
        state = int(result[0])
        retry_after = int(result[1])
        if state == 1:
            raise auth_error.RateLimitExceededError(
                retry_after_seconds=retry_after,
            )
        if state == 2:
            raise auth_error.RateLimitExceededError(
                retry_after_seconds=retry_after,
                detail="限流计数槽位已耗尽，请稍后重试",
            )

    def _consume_sync(
        self,
        digest: str,
        now_milliseconds: int,
        window_milliseconds: int,
    ) -> list[int]:
        """在 Redis Lua 脚本中完成容量、过期和次数检查。"""
        result = self._redis.eval(
            _REDIS_CONSUME_SCRIPT,
            3,
            f"{self._prefix}:bucket:{digest}",
            f"{self._prefix}:active",
            f"{self._prefix}:sequence",
            str(now_milliseconds),
            str(window_milliseconds),
            str(self._rule.limit),
            str(self._max_keys),
            digest,
        )
        if not isinstance(result, list) or len(result) != 2:
            raise RuntimeError("认证限流 Redis 返回无效结果")
        return [int(value) for value in result]


class AuthRateLimitService:
    """按认证入口和攻击维度隔离的限流服务。"""

    def __init__(
        self,
        *,
        login_ip: BoundedRateLimiter | RedisBoundedRateLimiter | None = None,
        login_identifier: BoundedRateLimiter | RedisBoundedRateLimiter | None = None,
        refresh_ip: BoundedRateLimiter | RedisBoundedRateLimiter | None = None,
        redis_url: str | None = None,
    ) -> None:
        """初始化登录与刷新入口的独立限流器。"""
        self._login_ip = login_ip or self._build_limiter(
            RateLimitRule(LOGIN_IP_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS),
            max_keys=IP_RATE_LIMIT_MAX_KEYS,
            bucket_name="login-ip",
            redis_url=redis_url,
        )
        self._login_identifier = login_identifier or self._build_limiter(
            RateLimitRule(LOGIN_IDENTIFIER_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS),
            max_keys=IDENTIFIER_RATE_LIMIT_MAX_KEYS,
            bucket_name="login-identifier",
            redis_url=redis_url,
        )
        self._refresh_ip = refresh_ip or self._build_limiter(
            RateLimitRule(REFRESH_RATE_LIMIT, REFRESH_RATE_WINDOW_SECONDS),
            max_keys=IP_RATE_LIMIT_MAX_KEYS,
            bucket_name="refresh-ip",
            redis_url=redis_url,
        )

    @staticmethod
    def _build_limiter(
        rule: RateLimitRule,
        *,
        max_keys: int,
        bucket_name: str,
        redis_url: str | None,
    ) -> BoundedRateLimiter | RedisBoundedRateLimiter:
        """按部署配置选择共享或进程内存储。"""
        if redis_url is None:
            return BoundedRateLimiter(rule, max_keys=max_keys)
        return RedisBoundedRateLimiter(
            rule,
            max_keys=max_keys,
            redis_url=redis_url,
            bucket_name=bucket_name,
        )

    async def check_login(self, client_ip: str, identifier: str) -> None:
        """同时限制登录来源 IP 与账号标识。"""
        await self._login_ip.consume(self._normalize_ip(client_ip))
        await self._login_identifier.consume(self._normalize_identifier(identifier))

    async def check_refresh(self, client_ip: str) -> None:
        """限制单个来源 IP 的令牌刷新频率。"""
        await self._refresh_ip.consume(self._normalize_ip(client_ip))

    @staticmethod
    def _normalize_ip(client_ip: str) -> str:
        """规范化客户端地址限流键。"""
        return client_ip.strip().casefold() or "unknown"

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """规范化登录账号限流键。"""
        return identifier.strip().casefold()
