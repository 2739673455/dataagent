"""分析 Worker 和 Docker 执行边界测试"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pandas as pd

from app.agents.contracts import AgentSessionKey
from app.agents.shared import sandbox_analysis_worker
from app.agents.shared.analysis_runtime import (
    SpecialistToolContext,
    run_sandbox_analysis,
)


class AnalysisArtifactSupportTest(unittest.IsolatedAsyncioTestCase):
    """验证 API 进程只编排 Docker 内的确定性计算"""

    @staticmethod
    def _context() -> SpecialistToolContext:
        return SpecialistToolContext(
            AgentSessionKey(
                user_id=12,
                conversation_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                analysis_id="sales",
                agent_type="attribution",
                session_id="region",
            )
        )

    async def test_executes_in_sandbox_without_downloading_csv(self) -> None:
        context = self._context()
        backend = AsyncMock()
        backend.aexecute.return_value = SimpleNamespace(
            exit_code=0,
            output='{"row_count":2,"result":{"total_change":-4}}',
        )
        with (
            patch(
                "app.agents.shared.analysis_runtime.docker_sandbox_manager.get_backend",
                AsyncMock(return_value=backend),
            ),
            patch(
                "app.agents.shared.analysis_runtime.docker_sandbox_manager.download_file",
                AsyncMock(),
            ) as download,
        ):
            result = await run_sandbox_analysis(
                context,
                operation="calculate_contribution",
                data_path="/analyses/sales/shared/source.csv",
                outputs={
                    "result": (
                        "/analyses/sales/sessions/attribution/region/result.json"
                    )
                },
                parameters={"top_n": 10},
            )

        self.assertEqual(result["row_count"], 2)
        download.assert_not_awaited()
        command = backend.aexecute.await_args.args[0]
        self.assertIn("python -c", command)
        self.assertNotIn("source.csv", command)

    async def test_rejects_cross_analysis_input_before_sandbox_call(self) -> None:
        context = SpecialistToolContext(
            AgentSessionKey(
                user_id=12,
                conversation_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
                analysis_id="sales",
                agent_type="attribution",
                session_id="region",
            )
        )
        with (
            patch(
                "app.agents.shared.analysis_runtime.docker_sandbox_manager.get_backend",
                AsyncMock(),
            ) as get_backend,
            self.assertRaisesRegex(ValueError, "current analysis"),
        ):
            await run_sandbox_analysis(
                context,
                operation="calculate_contribution",
                data_path="/analyses/other/shared/source.csv",
                outputs={
                    "result": (
                        "/analyses/sales/sessions/attribution/region/result.json"
                    )
                },
                parameters={},
            )

        get_backend.assert_not_awaited()


class SandboxAnalysisWorkerTest(unittest.TestCase):
    """验证容器 Worker 的计算和静态产物"""

    def test_generates_result_and_self_contained_chart(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "analyses/sales/shared/source.csv"
            source.parent.mkdir(parents=True)
            pd.DataFrame(
                {
                    "region": ["east", "east", "west", "west"],
                    "period": ["before", "after", "before", "after"],
                    "sales": [100, 70, 50, 40],
                }
            ).to_csv(source, index=False)
            base_payload = {
                "data_path": "/analyses/sales/shared/source.csv",
                "max_input_bytes": 1024 * 1024,
                "max_rows": 100,
            }
            with patch.object(
                sandbox_analysis_worker.os,
                "getcwd",
                return_value=directory,
            ):
                result = sandbox_analysis_worker.run(
                    {
                        **base_payload,
                        "operation": "calculate_contribution",
                        "outputs": {
                            "result": "/analyses/sales/sessions/attribution/region/result.json"
                        },
                        "parameters": {
                            "dimension_column": "region",
                            "metric_column": "sales",
                            "period_column": "period",
                            "baseline_value": "before",
                            "comparison_value": "after",
                            "top_n": 10,
                        },
                    }
                )
                chart = sandbox_analysis_worker.run(
                    {
                        **base_payload,
                        "operation": "render_chart",
                        "outputs": {
                            "html": "/analyses/sales/sessions/visualization/chart/chart.html",
                            "option": "/analyses/sales/sessions/visualization/chart/option.json",
                        },
                        "parameters": {
                            "x_column": "region",
                            "y_columns": ["sales"],
                            "chart_type": "bar",
                            "title": "Sales",
                        },
                    }
                )
                table = sandbox_analysis_worker.run(
                    {
                        **base_payload,
                        "operation": "render_interactive_table",
                        "outputs": {
                            "table": (
                                "/analyses/sales/sessions/visualization/table/"
                                "result.table.json"
                            )
                        },
                        "parameters": {"columns": ["region", "sales"], "max_rows": 3},
                    }
                )

            self.assertEqual(result["result"]["total_change"], -40.0)
            self.assertEqual(chart["row_count"], 4)
            chart_document = (
                root / "analyses/sales/sessions/visualization/chart/chart.html"
            ).read_text(encoding="utf-8")
            self.assertIn("<svg", chart_document)
            self.assertIn("default-src 'none'", chart_document)
            self.assertNotIn("<script", chart_document)
            self.assertNotIn("https://", chart_document)
            self.assertEqual(table["returned_row_count"], 3)
            table_payload = json.loads(
                (
                    root
                    / "analyses/sales/sessions/visualization/table/result.table.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(table_payload["format"], "dataagent-interactive-table-v1")
            self.assertEqual(table_payload["columns"], ["region", "sales"])
            self.assertTrue(table_payload["truncated"])
