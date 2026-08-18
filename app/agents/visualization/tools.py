"""静态 SVG 图表、图表配置和 HTML 报告工具"""

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool

from app.agents.shared.analysis_runtime import (
    get_specialist_tool_context,
    run_sandbox_analysis,
)

_INTERACTIVE_TABLE_MEDIA_TYPE = "application/vnd.dataagent.table+json"


@tool
async def render_chart(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    x_column: Annotated[str, "横轴字段"],
    y_columns: Annotated[list[str], "一个或多个数值纵轴字段"],
    chart_type: Annotated[Literal["line", "bar", "scatter"], "图表类型"],
    title: Annotated[str, "图表标题"],
) -> dict[str, Any]:
    """生成静态 SVG HTML 图表和可复用图表配置"""
    try:
        context = get_specialist_tool_context(runtime, "visualization")
        html_path = context.artifact_path("chart", "html")
        option_path = context.artifact_path("chart_option", "json")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="render_chart",
            data_path=data_path,
            outputs={"html": html_path, "option": option_path},
            parameters={
                "x_column": x_column,
                "y_columns": y_columns,
                "chart_type": chart_type,
                "title": title,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Chart rendering failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifacts": [
            {
                "path": html_path,
                "media_type": "text/html",
                "description": "Self-contained static SVG chart document",
            },
            {
                "path": option_path,
                "media_type": "application/json",
                "description": "ECharts option with source trace",
            },
        ],
        "row_count": sandbox_result["row_count"],
    }


@tool
async def build_report(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    title: Annotated[str, "报告标题"],
    summary: Annotated[str, "报告摘要"],
    findings: Annotated[list[str], "需要展示的已验证发现"],
    max_rows: Annotated[int, "数据预览最大行数，范围 1 到 1000"] = 200,
) -> dict[str, Any]:
    """生成带来源、发现和数据预览的可下载 HTML 报告"""
    try:
        context = get_specialist_tool_context(runtime, "visualization")
        output_path = context.artifact_path("report", "html")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="build_report",
            data_path=data_path,
            outputs={"html": output_path},
            parameters={
                "title": title,
                "summary": summary,
                "findings": findings,
                "max_rows": max_rows,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Report generation failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifact": {
            "path": output_path,
            "media_type": "text/html",
            "description": "Traceable HTML analysis report",
        },
        "row_count": sandbox_result["row_count"],
    }


@tool
async def render_interactive_table(
    runtime: ToolRuntime,
    data_path: Annotated[str, "输入 CSV 在当前会话沙盒中的绝对路径"],
    columns: Annotated[list[str], "需要展示的字段，空列表表示全部字段"],
    max_rows: Annotated[int, "可交互表格最大行数，范围 1 到 1000"] = 1000,
) -> dict[str, Any]:
    """生成由可信前端执行筛选、排序和分页的表格数据"""
    try:
        context = get_specialist_tool_context(runtime, "visualization")
        output_path = context.artifact_path("interactive_table", "table.json")
        sandbox_result = await run_sandbox_analysis(
            context,
            operation="render_interactive_table",
            data_path=data_path,
            outputs={"table": output_path},
            parameters={"columns": columns, "max_rows": max_rows},
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "message": f"Interactive table generation failed: {type(exc).__name__}: {exc}",
        }
    return {
        "status": "success",
        "artifact": {
            "path": output_path,
            "media_type": _INTERACTIVE_TABLE_MEDIA_TYPE,
            "description": "Sortable, filterable and paginated table data",
        },
        **sandbox_result,
    }
