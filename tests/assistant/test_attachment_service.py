"""产物下载的业务异常与 HTTP 响应。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.assistant import errors
from app.assistant.services.conversation import AttachmentService
from app.sandbox.errors import SandboxFileTooLargeError, SandboxPathError


@pytest.mark.parametrize(
    "failure, expected",
    [
        (SandboxPathError(), SandboxPathError),
        (SandboxFileTooLargeError(), SandboxFileTooLargeError),
        (FileNotFoundError(), errors.AttachmentNotFoundError),
    ],
)
def test_download_preserves_business_error(failure, expected):
    repository = MagicMock(get=AsyncMock(return_value=MagicMock()))
    sandbox = MagicMock(
        download_file=AsyncMock(side_effect=failure),
    )
    service = AttachmentService(repository, sandbox)
    args = (1, uuid4(), "sessions/analysis/analyst/report/a.csv")
    with pytest.raises(expected):
        asyncio.run(service.download(*args))


@pytest.mark.parametrize(
    "failure, status, error_type, title",
    [
        (
            SandboxFileTooLargeError(detail="文件大小超出限制: 11 > 10"),
            413,
            "sandbox-file-too-large",
            "文件过大",
        ),
        (
            SandboxPathError(detail="../report.csv"),
            403,
            "sandbox-path-invalid",
            "路径非法",
        ),
    ],
)
def test_download_returns_problem_response(failure, status, error_type, title):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.shared.errors.exc_handlers import register_exception_handlers

    service = AttachmentService(
        MagicMock(get=AsyncMock(return_value=MagicMock())),
        MagicMock(download_file=AsyncMock(side_effect=failure)),
    )
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/download")
    async def download():
        return await service.download(1, uuid4(), "report.csv")

    with TestClient(app) as client:
        response = client.get("/download")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == error_type
    assert response.json()["title"] == title
    assert response.json()["detail"] == failure.detail
