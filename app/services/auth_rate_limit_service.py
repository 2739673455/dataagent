"""认证接口的进程内有界速率限制"""

import asyncio
import hashlib
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.errors import auth_error

LOGIN_IP_RATE_LIMIT = 30
LOGIN_IDENTIFIER_RATE_LIMIT = 10
LOGIN_RATE_WINDOW_SECONDS = 60
REFRESH_RATE_LIMIT = 60
REFRESH_RATE_WINDOW_SECONDS = 60
IP_RATE_LIMIT_MAX_KEYS = 10_000
IDENTIFIER_RATE_LIMIT_MAX_KEYS = 50_000


@dataclass(frozen=True)
class RateLimitRule:
    """固定时间窗口内的请求上限"""

    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("rate limit must be positive")
        if self.window_seconds <= 0:
            raise ValueError("rate limit window must be positive")


@dataclass
class _RateBucket:
    """单个限流键的请求时间队列"""

    timestamps: deque[float] = field(default_factory=deque)
    last_seen: float = 0


class BoundedRateLimiter:
    """按最近使用顺序限制键数量的滑动窗口限流器"""

    def __init__(
        self,
        rule: RateLimitRule,
        *,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self._rule = rule
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, _RateBucket] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def tracked_keys(self) -> int:
        """返回当前跟踪的限流键数量"""
        return len(self._buckets)

    async def consume(self, key: str) -> None:
        """消费一次请求额度"""
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
                    math.ceil(
                        bucket.timestamps[0] + self._rule.window_seconds - now
                    ),
                )
                raise auth_error.RateLimitExceededError(
                    retry_after_seconds=retry_after
                )
            bucket.timestamps.append(now)

    def _remove_expired_buckets(self, cutoff: float) -> None:
        """从最近最少使用端清除过期键"""
        while self._buckets:
            key, bucket = next(iter(self._buckets.items()))
            if bucket.last_seen > cutoff:
                break
            self._buckets.pop(key)

    @staticmethod
    def _prune_timestamps(bucket: _RateBucket, cutoff: float) -> None:
        """清除滑动窗口外的请求记录"""
        while bucket.timestamps and bucket.timestamps[0] <= cutoff:
            bucket.timestamps.popleft()

    def _capacity_retry_after(self, now: float) -> int:
        """计算最早限流键自然过期前的等待时间"""
        oldest = next(iter(self._buckets.values()))
        return max(
            1,
            math.ceil(oldest.last_seen + self._rule.window_seconds - now),
        )


class AuthRateLimitService:
    """按认证入口和攻击维度隔离的限流服务"""

    def __init__(
        self,
        *,
        login_ip: BoundedRateLimiter | None = None,
        login_identifier: BoundedRateLimiter | None = None,
        refresh_ip: BoundedRateLimiter | None = None,
    ) -> None:
        self._login_ip = login_ip or BoundedRateLimiter(
            RateLimitRule(LOGIN_IP_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS),
            max_keys=IP_RATE_LIMIT_MAX_KEYS,
        )
        self._login_identifier = login_identifier or BoundedRateLimiter(
            RateLimitRule(LOGIN_IDENTIFIER_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS),
            max_keys=IDENTIFIER_RATE_LIMIT_MAX_KEYS,
        )
        self._refresh_ip = refresh_ip or BoundedRateLimiter(
            RateLimitRule(REFRESH_RATE_LIMIT, REFRESH_RATE_WINDOW_SECONDS),
            max_keys=IP_RATE_LIMIT_MAX_KEYS,
        )

    async def check_login(self, client_ip: str, identifier: str) -> None:
        """同时限制登录来源 IP 与账号标识"""
        await self._login_ip.consume(self._normalize_ip(client_ip))
        await self._login_identifier.consume(self._normalize_identifier(identifier))

    async def check_refresh(self, client_ip: str) -> None:
        """限制单个来源 IP 的令牌刷新频率"""
        await self._refresh_ip.consume(self._normalize_ip(client_ip))

    @staticmethod
    def _normalize_ip(client_ip: str) -> str:
        """规范化客户端地址限流键"""
        return client_ip.strip().casefold() or "unknown"

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """规范化登录账号限流键"""
        return identifier.strip().casefold()
