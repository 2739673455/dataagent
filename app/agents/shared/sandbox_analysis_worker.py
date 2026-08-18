"""在会话 Docker 沙盒内执行确定性表格分析"""

# pyright: reportArgumentType=false

from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any, cast

import pandas as pd

from app.analysis.anomaly_detection import detect_anomalies, validate_time_series
from app.analysis.attribution import calculate_contribution
from app.analysis.visualization import (
    ChartType,
    build_interactive_table,
    build_report,
    render_chart,
)


def _workspace_path(path: str, *, create_parent: bool = False) -> str:
    root = os.path.realpath(os.getcwd())
    relative = path.lstrip("/")
    candidate = os.path.abspath(os.path.join(root, relative))
    if os.path.commonpath((root, candidate)) != root:
        raise ValueError("path escapes the conversation workspace")
    if create_parent:
        os.makedirs(os.path.dirname(candidate), mode=0o700, exist_ok=True)
        parent = os.path.realpath(os.path.dirname(candidate))
        if os.path.commonpath((root, parent)) != root:
            raise ValueError("output parent escapes the conversation workspace")
    else:
        candidate = os.path.realpath(candidate)
        if os.path.commonpath((root, candidate)) != root:
            raise ValueError("input path escapes the conversation workspace")
    return candidate


def _load_csv(payload: dict[str, Any]) -> pd.DataFrame:
    path = _workspace_path(str(payload["data_path"]))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size > int(payload["max_input_bytes"]):
            raise ValueError("analysis input exceeds byte limit")
        frame = pd.read_csv(source)
        if os.fstat(source.fileno()).st_size > int(payload["max_input_bytes"]):
            raise ValueError("analysis input exceeds byte limit")
    if len(frame) > int(payload["max_rows"]):
        raise ValueError("analysis input exceeds row limit")
    return frame


def _write_text(path: str, content: str) -> None:
    target = _workspace_path(path, create_parent=True)
    with open(target, "x", encoding="utf-8") as output:
        output.write(content)


def _compact_quality(quality: dict[str, Any]) -> dict[str, Any]:
    gaps = list(quality.get("time_gaps", []))
    return {
        **quality,
        "time_gaps": gaps[:50],
        "time_gap_count": len(gaps),
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """分派一次受控分析操作并写入声明的产物"""
    operation = str(payload["operation"])
    parameters = dict(payload["parameters"])
    outputs = dict(payload["outputs"])
    data_path = str(payload["data_path"])
    frame = _load_csv(payload)

    if operation == "calculate_contribution":
        result = calculate_contribution(
            frame,
            dimension_column=str(parameters["dimension_column"]),
            metric_column=str(parameters["metric_column"]),
            period_column=str(parameters["period_column"]),
            baseline_value=str(parameters["baseline_value"]),
            comparison_value=str(parameters["comparison_value"]),
            top_n=int(parameters["top_n"]),
        )
        _write_text(
            str(outputs["result"]),
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
        )
        rows = list(result["rows"])
        compact_rows = [
            {
                **row,
                "dimension_value": str(row["dimension_value"])[:512],
            }
            for row in rows[:50]
        ]
        compact_result = {
            **result,
            "rows": compact_rows,
            "returned_row_count": len(rows),
        }
        return {"row_count": len(frame), "result": compact_result}

    if operation == "validate_time_series":
        result = validate_time_series(
            frame,
            time_column=str(parameters["time_column"]),
            value_column=str(parameters["value_column"]),
        )
        _write_text(
            str(outputs["result"]),
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
        )
        return {
            "row_count": len(frame),
            "result": _compact_quality(result),
        }

    if operation == "detect_point_anomalies":
        result = detect_anomalies(
            frame,
            time_column=str(parameters["time_column"]),
            value_column=str(parameters["value_column"]),
            threshold=float(parameters["threshold"]),
        )
        _write_text(
            str(outputs["result"]),
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
        )
        compact_result = {
            "method": result["method"],
            "threshold": result["threshold"],
            "quality": _compact_quality(result["quality"]),
            "observation_count": result["observation_count"],
            "point_anomaly_count": len(result["point_anomalies"]),
            "change_point_count": len(result["change_points"]),
            "point_anomalies": result["point_anomalies"][:50],
            "change_points": result["change_points"][:50],
        }
        return {"row_count": len(frame), "result": compact_result}

    if operation == "render_chart":
        option, document = render_chart(
            frame,
            x_column=str(parameters["x_column"]),
            y_columns=[str(value) for value in parameters["y_columns"]],
            chart_type=cast(ChartType, str(parameters["chart_type"])),
            title=str(parameters["title"]),
            source_path=data_path,
        )
        _write_text(
            str(outputs["option"]),
            json.dumps(option, ensure_ascii=False, indent=2),
        )
        _write_text(str(outputs["html"]), document)
        return {"row_count": len(frame)}

    if operation == "build_report":
        document = build_report(
            frame,
            title=str(parameters["title"]),
            summary=str(parameters["summary"]),
            findings=[str(value) for value in parameters["findings"]],
            source_path=data_path,
            max_rows=int(parameters["max_rows"]),
        )
        _write_text(str(outputs["html"]), document)
        return {"row_count": len(frame)}

    if operation == "render_interactive_table":
        table = build_interactive_table(
            frame,
            columns=[str(value) for value in parameters.get("columns", [])],
            max_rows=int(parameters["max_rows"]),
            source_path=data_path,
        )
        _write_text(
            str(outputs["table"]),
            json.dumps(table, ensure_ascii=False, separators=(",", ":")),
        )
        return {
            "row_count": len(frame),
            "returned_row_count": len(table["rows"]),
        }

    raise ValueError(f"unsupported operation: {operation}")


if __name__ == "__main__":
    request = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode("utf-8"))
    print(json.dumps(run(request), ensure_ascii=False, default=str))
