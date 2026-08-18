"""时序异常和数据质量工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from app.agents.shared.analysis_runtime import (
    get_specialist_tool_context,
    run_sandbox_analysis,
)


@tool
async def validate_time_series(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    time_column: Annotated[str, "时间字段"],
    value_column: Annotated[str, "需要检查的数值字段"],
) -> dict[str, Any]:
    """校验时序字段、缺失值、重复时间和时间断层"""
    try:
        context = get_specialist_tool_context(runtime, "anomaly_detection")
        output_path = context.artifact_path("time_series_quality", "json")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="validate_time_series",
            data_path=data_path,
            outputs={"result": output_path},
            parameters={
                "time_column": time_column,
                "value_column": value_column,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Time series validation failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifact": {
            "path": output_path,
            "media_type": "application/json",
            "description": "Time series data quality report",
        },
        "result": sandbox_result["result"],
    }


@tool
async def detect_point_anomalies(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    time_column: Annotated[str, "时间字段"],
    value_column: Annotated[str, "需要检测的数值字段"],
    threshold: Annotated[float, "稳健 Z 分数阈值，范围 2 到 10"] = 3.5,
) -> dict[str, Any]:
    """检测点异常和变化点并保存包含数据质量的 JSON 证据"""
    try:
        context = get_specialist_tool_context(runtime, "anomaly_detection")
        output_path = context.artifact_path("anomalies", "json")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="detect_point_anomalies",
            data_path=data_path,
            outputs={"result": output_path},
            parameters={
                "time_column": time_column,
                "value_column": value_column,
                "threshold": threshold,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Anomaly detection failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifact": {
            "path": output_path,
            "media_type": "application/json",
            "description": "Robust point anomaly and change point detection",
        },
        "result": sandbox_result["result"],
    }
