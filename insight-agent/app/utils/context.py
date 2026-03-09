from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
client_ip_ctx: ContextVar[str] = ContextVar("client_ip", default="")
method_ctx: ContextVar[str] = ContextVar("method", default="")
path_ctx: ContextVar[str] = ContextVar("path", default="")
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
status_ctx: ContextVar[str | None] = ContextVar("status", default=None)
response_time_ms_ctx: ContextVar[float | None] = ContextVar(
    "response_time_ms", default=None
)
