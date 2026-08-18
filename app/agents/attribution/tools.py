"""归因贡献分解工具"""

from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool

from app.agents.shared.analysis_runtime import (
    get_specialist_tool_context,
    run_sandbox_analysis,
)


@tool
async def calculate_contribution(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    dimension_column: Annotated[str, "需要分解的维度字段"],
    metric_column: Annotated[str, "可加和的数值指标字段"],
    period_column: Annotated[str, "区分基准期和对比期的字段"],
    baseline_value: Annotated[str, "基准期字段值"],
    comparison_value: Annotated[str, "对比期字段值"],
    top_n: Annotated[int, "按变化绝对值返回的最大维度成员数"] = 50,
) -> dict[str, Any]:
    """计算两个时期之间各维度成员的变化贡献并保存 JSON 证据"""
    try:
        context = get_specialist_tool_context(runtime, "attribution")
        output_path = context.artifact_path("contribution", "json")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="calculate_contribution",
            data_path=data_path,
            outputs={"result": output_path},
            parameters={
                "dimension_column": dimension_column,
                "metric_column": metric_column,
                "period_column": period_column,
                "baseline_value": baseline_value,
                "comparison_value": comparison_value,
                "top_n": top_n,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Contribution calculation failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifact": {
            "path": output_path,
            "media_type": "application/json",
            "description": "Deterministic additive change contribution",
        },
        "result": sandbox_result["result"],
    }
