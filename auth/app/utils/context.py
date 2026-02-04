from contextvars import ContextVar

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)
method_ctx: ContextVar[str | None] = ContextVar("method", default=None)
path_ctx: ContextVar[str | None] = ContextVar("path", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
status_ctx: ContextVar[str | None] = ContextVar("status", default=None)
response_time_ms_ctx: ContextVar[float | None] = ContextVar(
    "response_time_ms", default=None
)
