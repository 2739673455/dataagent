"""HTTP 请求追踪上下文测试"""

import unittest

from fastapi import Request, Response

from app.shared.observability import context, trace


class TraceMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_sets_response_headers_and_restores_outer_context(self) -> None:
        request_token = context.request_id_ctx.set("outer-request")
        user_token = context.user_id_ctx.set("outer-user")
        observed: dict[str, str | None] = {}

        async def call_next(_: Request) -> Response:
            observed["request_id"] = context.request_id_ctx.get()
            observed["trace_id"] = context.trace_id_ctx.get()
            observed["user_id"] = context.user_id_ctx.get()
            context.user_id_ctx.set("request-user")
            return Response()

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/health",
                "headers": [
                    (b"x-request-id", b"request-1"),
                    (b"x-trace-id", b"trace-1"),
                ],
                "client": ("127.0.0.1", 1234),
                "server": ("test", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )
        try:
            response = await trace.middleware(request, call_next)

            self.assertEqual(
                observed,
                {
                    "request_id": "request-1",
                    "trace_id": "trace-1",
                    "user_id": None,
                },
            )
            self.assertEqual(response.headers["X-Request-ID"], "request-1")
            self.assertEqual(response.headers["X-Trace-ID"], "trace-1")
            self.assertEqual(context.request_id_ctx.get(), "outer-request")
            self.assertEqual(context.user_id_ctx.get(), "outer-user")
        finally:
            context.user_id_ctx.reset(user_token)
            context.request_id_ctx.reset(request_token)

    async def test_restores_context_when_request_fails(self) -> None:
        token = context.path_ctx.set("/outer")

        async def call_next(_: Request) -> Response:
            raise RuntimeError("route failed")

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/failed",
                "headers": [],
                "client": None,
                "server": ("test", 80),
                "scheme": "http",
                "query_string": b"",
            }
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "route failed"):
                await trace.middleware(request, call_next)
            self.assertEqual(context.path_ctx.get(), "/outer")
        finally:
            context.path_ctx.reset(token)


if __name__ == "__main__":
    unittest.main()
