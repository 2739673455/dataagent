"""水位 JSON 往返转换和类型校验。"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.metadata.services.index import MetaIndexService


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 27, 12, 30, tzinfo=UTC),
        date(2026, 9, 27),
        Decimal("123456789.123456789"),
        True,
        False,
        12345678901234567890,
        1.25,
        "cursor",
    ],
)
def test_cursor_round_trip_preserves_value_and_type(value):
    payload = json.loads(json.dumps(MetaIndexService._serialize_cursor(value)))
    restored = MetaIndexService._deserialize_cursor(payload)
    assert restored == value
    assert type(restored) is type(value)


def test_float_cursor_accepts_integer_json_value():
    restored = MetaIndexService._deserialize_cursor({"type": "float", "value": 2})
    assert restored == 2.0
    assert type(restored) is float


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "int", "value": True},
        {"type": "float", "value": False},
        {"type": "bool", "value": 1},
        {"type": "str", "value": 1},
        {"type": "int", "value": "1"},
        {"type": "unknown", "value": 1},
        {"type": [], "value": 1},
        {},
    ],
)
def test_invalid_cursor_payload_is_rejected(payload):
    with pytest.raises(ValueError, match="取值索引游标状态格式无效"):
        MetaIndexService._deserialize_cursor(payload)


@pytest.mark.parametrize("value", [None, [], {}])
def test_unsupported_cursor_value_is_rejected(value):
    with pytest.raises(TypeError, match="不支持的取值索引游标类型"):
        MetaIndexService._serialize_cursor(value)
