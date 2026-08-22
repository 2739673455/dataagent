from __future__ import annotations

import datetime
import io
import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from loguru import logger

from app.core import context
from app.core.log import (
    _console_formatter,
    _json_formatter,
    setup_logger,
)

if TYPE_CHECKING:
    from loguru import Record


def build_record(
    *,
    extra: dict[str, Any] | None = None,
    exception: object | None = None,
) -> dict[str, Any]:
    return {
        "time": datetime.datetime(
            2026,
            8,
            22,
            10,
            26,
            29,
            925000,
            tzinfo=datetime.UTC,
        ),
        "level": SimpleNamespace(name="ERROR"),
        "name": "app.services.chat_service",
        "function": "stream",
        "line": 100,
        "message": "Request failed",
        "extra": extra or {},
        "exception": exception,
    }


class TestLogJsonFormatter(unittest.TestCase):
    def test_json_formatter_includes_location(self) -> None:
        record = build_record()

        template = _json_formatter(cast("Record", record))
        parsed = json.loads(record["extra"]["_json_line"])

        self.assertEqual(template, "{extra[_json_line]}\n")
        self.assertEqual(parsed["time"], "2026-08-22 10:26:29.925")
        self.assertEqual(parsed["level"], "ERROR")
        self.assertEqual(
            parsed["location"],
            "app.services.chat_service:stream:100",
        )
        self.assertEqual(parsed["message"], "Request failed")
        self.assertNotIn("_json_line", parsed)

    def test_json_formatter_keeps_exception_and_context(self) -> None:
        tokens = [
            (context.request_id_ctx, context.request_id_ctx.set("request-1")),
            (context.trace_id_ctx, context.trace_id_ctx.set("trace-1")),
            (context.client_ip_ctx, context.client_ip_ctx.set("127.0.0.1")),
            (context.method_ctx, context.method_ctx.set("POST")),
            (context.path_ctx, context.path_ctx.set("/api/v1/chat")),
            (context.user_id_ctx, context.user_id_ctx.set("user-1")),
        ]
        try:
            try:
                raise ValueError("invalid value")
            except ValueError as error:
                exception = SimpleNamespace(
                    type=type(error),
                    value=error,
                    traceback=error.__traceback__,
                )

            record = build_record(
                extra={
                    "detail": "数据库连接失败",
                    "duration_ms": 12,
                },
                exception=exception,
            )

            _json_formatter(cast("Record", record))
            parsed = json.loads(record["extra"]["_json_line"])

            self.assertEqual(parsed["request_id"], "request-1")
            self.assertEqual(parsed["trace_id"], "trace-1")
            self.assertEqual(parsed["client_ip"], "127.0.0.1")
            self.assertEqual(parsed["method"], "POST")
            self.assertEqual(parsed["path"], "/api/v1/chat")
            self.assertEqual(parsed["user_id"], "user-1")
            self.assertEqual(parsed["detail"], "数据库连接失败")
            self.assertEqual(parsed["duration_ms"], 12)
            self.assertIn("ValueError: invalid value", parsed["exception"])
            self.assertIs(record["exception"], exception)
            self.assertNotIn("_console_detail", record["extra"])
            self.assertNotIn("exception_text", record["extra"])
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)

    def test_json_formatter_preserves_falsey_values_and_core_fields(self) -> None:
        record = build_record(
            extra={
                "duration_ms": 0,
                "cache_hit": False,
                "tags": [],
                "opaque": object(),
                "time": "spoofed-time",
                "level": "spoofed-level",
                "location": "spoofed-location",
                "message": "spoofed-message",
                "_json_line": "spoofed-json",
            }
        )

        _json_formatter(cast("Record", record))
        parsed = json.loads(record["extra"]["_json_line"])

        self.assertEqual(parsed["duration_ms"], 0)
        self.assertIs(parsed["cache_hit"], False)
        self.assertEqual(parsed["tags"], [])
        self.assertIsInstance(parsed["opaque"], str)
        self.assertEqual(parsed["time"], "2026-08-22 10:26:29.925")
        self.assertEqual(parsed["level"], "ERROR")
        self.assertEqual(parsed["message"], "Request failed")
        self.assertEqual(
            parsed["location"],
            "app.services.chat_service:stream:100",
        )


class TestConsoleFormatter(unittest.TestCase):
    def test_console_formatter_uses_safe_placeholders_and_whitelist(self) -> None:
        detail = "数据库 {down} <red>shutdown</red>"
        record = build_record(
            extra={
                "detail": detail,
                "status": 500,
                "problem_type": "internal-server-error",
                "exc_type": "ValueError",
                "secret": "must-not-be-rendered",
            }
        )

        template = _console_formatter(cast("Record", record))

        self.assertIn("{extra[detail]}", template)
        self.assertIn("status={extra[status]}", template)
        self.assertIn("problem_type={extra[problem_type]}", template)
        self.assertIn("exc_type={extra[exc_type]}", template)
        self.assertNotIn(detail, template)
        self.assertNotIn("secret", template)
        self.assertTrue(template.endswith("\n{exception}"))

    def test_handlers_keep_json_single_line_and_console_traceback(self) -> None:
        console = io.StringIO()
        jsonl = io.StringIO()
        marker = "log-formatter-contract"

        def only_contract_record(record: Record) -> bool:
            return record["extra"].get("marker") == marker

        console_handler = logger.add(
            console,
            format=_console_formatter,
            filter=only_contract_record,
            colorize=True,
            backtrace=False,
            diagnose=False,
            catch=False,
        )
        json_handler = logger.add(
            jsonl,
            format=_json_formatter,
            filter=only_contract_record,
            colorize=False,
            backtrace=False,
            diagnose=False,
            catch=False,
        )
        try:
            try:
                raise ValueError("bad {value} <red>")
            except ValueError as error:
                logger.bind(marker=marker).opt(exception=error).error(
                    "Request failed",
                    detail="数据库 {down} <red>shutdown</red>",
                    status=500,
                    problem_type="internal-server-error",
                    exc_type="ValueError",
                )
        finally:
            logger.remove(console_handler)
            logger.remove(json_handler)

        console_output = re.sub(r"\x1b\[[0-9;]*m", "", console.getvalue())
        json_lines = jsonl.getvalue().splitlines()
        self.assertIn("Traceback", console_output)
        self.assertIn("ValueError: bad {value} <red>", console_output)
        self.assertIn("数据库 {down} <red>shutdown</red>", console_output)
        self.assertIn("status=500", console_output)
        self.assertEqual(len(json_lines), 1)
        payload = json.loads(json_lines[0])
        self.assertIn("ValueError: bad {value} <red>", payload["exception"])


class TestSetupLogger(unittest.TestCase):
    def setUp(self) -> None:
        setup_logger.cache_clear()

    def tearDown(self) -> None:
        setup_logger.cache_clear()

    def test_setup_logger_configures_handlers_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            with (
                patch("app.core.log.LOG_DIR", log_dir),
                patch("app.core.log.logger.configure") as configure,
            ):
                setup_logger()
                setup_logger()

                self.assertTrue(log_dir.is_dir())

        configure.assert_called_once()
        options = configure.call_args.kwargs
        self.assertNotIn("patcher", options)
        self.assertEqual(len(options["handlers"]), 2)

        console_handler = options["handlers"][0]
        self.assertIs(console_handler["sink"], sys.stdout)
        self.assertIs(console_handler["format"], _console_formatter)
        self.assertIs(console_handler["backtrace"], False)
        self.assertIs(console_handler["diagnose"], False)

        json_handler = options["handlers"][1]
        self.assertEqual(
            json_handler["sink"],
            str(log_dir / "{time:YYYY-MM-DD}.jsonl"),
        )
        self.assertIs(json_handler["format"], _json_formatter)
        self.assertIs(json_handler["backtrace"], False)
        self.assertIs(json_handler["diagnose"], False)
