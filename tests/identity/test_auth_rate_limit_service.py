"""认证接口有界速率限制测试。"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.identity import errors as auth_error
from app.identity.api.auth.dependencies import (
    _get_auth_rate_limit_service,
    _get_auth_service,
)
from app.identity.api.auth.router import router
from app.identity.services.rate_limit import (
    AuthRateLimitService,
    BoundedRateLimiter,
    RateLimitRule,
)
from app.shared.errors.base import ProblemError
from app.shared.errors.exc_handlers import (
    _problem_error_handler,
    register_exception_handlers,
)


class FakeClock:
    """可推进的单调时钟。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """推进测试时钟。"""
        self.now += seconds


def build_limiter(
    limit: int,
    *,
    max_keys: int = 100,
    clock: FakeClock | None = None,
) -> BoundedRateLimiter:
    """构造测试限流器。"""
    return BoundedRateLimiter(
        RateLimitRule(limit, 60),
        max_keys=max_keys,
        clock=clock or FakeClock(),
    )


class BoundedRateLimiterTest(unittest.IsolatedAsyncioTestCase):
    """验证限流窗口、并发安全和键容量。"""

    async def test_window_limit_is_atomic_under_concurrency(self) -> None:
        limiter = build_limiter(5)

        results = await asyncio.gather(
            *(limiter.consume("same-key") for _ in range(10)),
            return_exceptions=True,
        )

        self.assertEqual(sum(result is None for result in results), 5)
        self.assertEqual(
            sum(
                isinstance(result, auth_error.RateLimitExceededError)
                for result in results
            ),
            5,
        )

    async def test_key_count_is_bounded_and_recovers_after_window(self) -> None:
        clock = FakeClock()
        limiter = build_limiter(2, max_keys=2, clock=clock)
        await limiter.consume("first")
        await limiter.consume("second")

        with self.assertRaises(auth_error.RateLimitExceededError) as raised:
            await limiter.consume("third")

        self.assertEqual(limiter.tracked_keys, 2)
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.extensions["retry_after_seconds"], 60)

        clock.advance(61)
        await limiter.consume("third")
        self.assertEqual(limiter.tracked_keys, 1)


class AuthRateLimitServiceTest(unittest.IsolatedAsyncioTestCase):
    """验证认证攻击维度相互隔离且同时生效。"""

    async def test_login_is_limited_by_ip_across_identifiers(self) -> None:
        service = AuthRateLimitService(
            login_ip=build_limiter(1),
            login_identifier=build_limiter(10),
        )
        await service.check_login("192.0.2.1", "first@example.com")

        with self.assertRaises(auth_error.RateLimitExceededError):
            await service.check_login("192.0.2.1", "second@example.com")

    async def test_login_identifier_is_normalized_across_ips(self) -> None:
        service = AuthRateLimitService(
            login_ip=build_limiter(10),
            login_identifier=build_limiter(1),
        )
        await service.check_login("192.0.2.1", "User@Example.com")

        with self.assertRaises(auth_error.RateLimitExceededError):
            await service.check_login("192.0.2.2", " user@example.COM ")


class AuthRateLimitRouterTest(unittest.IsolatedAsyncioTestCase):
    """验证认证路由返回 RFC Problem 429 响应。"""

    def test_public_auth_router_has_no_admin_bootstrap_endpoint(self) -> None:
        paths = {route.path for route in router.routes if isinstance(route, APIRoute)}

        self.assertTrue(all("bootstrap" not in path for path in paths))

    @staticmethod
    def build_app(service: MagicMock, limiter: AuthRateLimitService) -> FastAPI:
        """构造替换认证服务和限流器的测试应用。"""
        app = FastAPI()
        register_exception_handlers(app)

        async def async_problem_handler(request, exc):
            return _problem_error_handler(request, exc)

        async def override_auth_service():
            return service

        async def override_rate_limit_service():
            return limiter

        app.add_exception_handler(ProblemError, async_problem_handler)
        app.include_router(router, prefix="/api/v1/auth")
        app.dependency_overrides[_get_auth_service] = override_auth_service
        app.dependency_overrides[_get_auth_rate_limit_service] = (
            override_rate_limit_service
        )
        return app

    async def test_login_uses_normalized_identifier_rate_limit(self) -> None:
        service = MagicMock()
        service.login = AsyncMock(side_effect=auth_error.InvalidCredentialsError())
        limiter = AuthRateLimitService(
            login_ip=build_limiter(10),
            login_identifier=build_limiter(1),
        )
        app = self.build_app(service, limiter)

        async with AsyncClient(
            transport=ASGITransport(app=app, client=("192.0.2.10", 1234)),
            base_url="http://test",
        ) as client:
            first = await client.post(
                "/api/v1/auth/login",
                json={"identifier": "User@Example.com", "password": "wrong"},
            )
            second = await client.post(
                "/api/v1/auth/login",
                json={"identifier": " user@example.COM ", "password": "wrong"},
            )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["content-type"], "application/problem+json")
        self.assertEqual(second.headers["retry-after"], "60")
        self.assertEqual(second.json()["type"], "rate-limit-exceeded")
        self.assertEqual(second.json()["retry_after_seconds"], 60)
        service.login.assert_awaited_once()

    def test_public_auth_router_has_no_register_endpoint(self) -> None:
        paths = {route.path for route in router.routes if isinstance(route, APIRoute)}
        self.assertNotIn("/register", paths)

    async def test_refresh_is_rate_limited_by_client_ip(self) -> None:
        service = MagicMock()
        service.refresh = AsyncMock(side_effect=auth_error.InvalidTokenError())
        limiter = AuthRateLimitService(refresh_ip=build_limiter(1))
        app = self.build_app(service, limiter)
        body = {"refresh_token": "invalid-refresh-token"}

        async with AsyncClient(
            transport=ASGITransport(app=app, client=("192.0.2.30", 1234)),
            base_url="http://test",
        ) as client:
            first = await client.post("/api/v1/auth/refresh", json=body)
            second = await client.post("/api/v1/auth/refresh", json=body)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        service.refresh.assert_awaited_once()
