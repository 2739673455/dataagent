"""取值同步水位的 JSON 往返、类型边界与回看规则。"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.metadata.indexing import MetaIndexService


@pytest.mark.parametrize(
    "value,tag",
    [
        (True, "bool"),
        (False, "bool"),
        (0, "int"),
        (2**60, "int"),
        (-2.5, "float"),
        ("水位", "str"),
        ("", "str"),
        (date(2026, 9, 27), "date"),
        # Doris DATETIME 也可能返回无时区值，必须原样恢复。
        (datetime(2026, 9, 27, 12, 30, 10, 123456), "datetime"),  # noqa: DTZ001
        (datetime(2026, 9, 27, 12, 30, tzinfo=UTC), "datetime"),
        (Decimal("1234567890.123456789"), "decimal"),
    ],
)
def test_cursor_json_round_trip_preserves_value_and_type(value, tag):
    payload = MetaIndexService._serialize_cursor(value)
    assert payload["type"] == tag
    restored = MetaIndexService._deserialize_cursor(json.loads(json.dumps(payload)))
    assert type(restored) is type(value)
    assert restored == value


@pytest.mark.parametrize("value", [0, 4, -3.5])
def test_float_cursor_accepts_numbers_and_restores_float(value):
    restored = MetaIndexService._deserialize_cursor({"type": "float", "value": value})
    assert type(restored) is float
    assert restored == value


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "int", "value": True},
        {"type": "int", "value": False},
        {"type": "float", "value": True},
        {"type": "float", "value": False},
        {"type": "bool", "value": 1},
        {"type": "int", "value": 1.0},
        {"type": "int", "value": "1"},
        {"type": "float", "value": "1.0"},
        {"type": "str", "value": 1},
        {"type": "date", "value": 1},
        {"type": "datetime", "value": None},
        {"type": "decimal", "value": 1.5},
        {"type": "unknown", "value": 1},
        {"type": [], "value": 1},
        {"type": "int"},
        {},
    ],
)
def test_cursor_rejects_mismatched_or_missing_type(payload):
    with pytest.raises(ValueError, match="取值索引游标状态格式无效"):
        MetaIndexService._deserialize_cursor(payload)


@pytest.mark.parametrize("value", [None, [], {}, b"1", object()])
def test_cursor_rejects_unsupported_values(value):
    with pytest.raises(TypeError, match="不支持的取值索引游标类型"):
        MetaIndexService._serialize_cursor(value)


@pytest.mark.parametrize("base,value", [(int, 1), (float, 1.5), (str, "1")])
def test_cursor_rejects_basic_type_subclasses(base, value):
    subclass_value = type("CustomValue", (base,), {})(value)
    with pytest.raises(TypeError, match="不支持的取值索引游标类型"):
        MetaIndexService._serialize_cursor(subclass_value)
    with pytest.raises(ValueError, match="取值索引游标状态格式无效"):
        MetaIndexService._deserialize_cursor(
            {"type": base.__name__, "value": subclass_value}
        )


@pytest.mark.parametrize(
    "value,seconds,expected",
    [
        (
            datetime(2026, 9, 27, 12, tzinfo=UTC),
            60,
            datetime(2026, 9, 27, 11, 59, tzinfo=UTC),
        ),
        (date(2026, 9, 27), 1, date(2026, 9, 26)),
        (date(2026, 9, 27), 86401, date(2026, 9, 25)),
        (42, 60, 42),
        (Decimal("1.2"), 60, Decimal("1.2")),
        ("cursor", 60, "cursor"),
    ],
)
def test_restored_cursor_preserves_lookback_rules(value, seconds, expected):
    restored = MetaIndexService._deserialize_cursor(
        json.loads(json.dumps(MetaIndexService._serialize_cursor(value)))
    )
    assert MetaIndexService._lookback_lower_bound(restored, seconds) == expected
