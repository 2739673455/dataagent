"""查询经验管理员接口测试"""

import unittest
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from starlette.types import ExceptionHandler

from app.identity.api.auth.dependencies import _get_current_user
from app.identity.services.auth import AuthenticatedUser
from app.query.api.admin.dependencies import _get_query_experience_management_service
from app.query.api.admin.router import router
from app.query.services.experience_management import QueryExperienceManagementService
from app.shared.errors.base import ProblemError
from app.shared.errors.exc_handlers import (
    _problem_error_handler,
    _validation_error_handler,
)


class _Service:
    async def list_overviews(self, **_: object) -> tuple[list[object], int]:
        return [], 0


def _user(*, is_admin: bool) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=7,
        username="admin" if is_admin else "member",
        email="user@example.com",
        auth_version=1,
        is_active=True,
        is_admin=is_admin,
        doris_role_name="analyst",
        created_at=datetime.now(UTC),
    )


def _app(*, is_admin: bool) -> FastAPI:
    app = FastAPI()

    async def problem_handler(request: Request, exc: ProblemError):
        return _problem_error_handler(request, exc)

    async def validation_handler(request: Request, exc: RequestValidationError):
        return _validation_error_handler(request, exc)

    app.add_exception_handler(ProblemError, cast(ExceptionHandler, problem_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_handler),
    )
    app.include_router(router, prefix="/api/v1/admin/query-experiences")

    async def current_user() -> AuthenticatedUser:
        return _user(is_admin=is_admin)

    async def service() -> QueryExperienceManagementService:
        return cast(QueryExperienceManagementService, cast(Any, _Service()))

    app.dependency_overrides[_get_current_user] = current_user
    app.dependency_overrides[_get_query_experience_management_service] = service
    return app


class QueryExperienceAdminApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_list_experiences(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=_app(is_admin=True)),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/admin/query-experiences")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"items": [], "total": 0, "limit": 20, "offset": 0, "has_more": False},
        )

    async def test_regular_user_cannot_list_experiences(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=_app(is_admin=False)),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/admin/query-experiences")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["type"], "permission-denied")

    async def test_invalid_status_returns_problem_details(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=_app(is_admin=True)),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/admin/query-experiences",
                params={"status": "unknown"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["type"], "validation-error")
