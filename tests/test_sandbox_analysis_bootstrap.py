"""验证确定性分析内核可以注入独立 Sandbox Python 进程"""

import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from app.agents.shared.analysis_runtime import _WORKER_BOOTSTRAP


class SandboxAnalysisBootstrapTest(unittest.TestCase):
    """验证 Worker 使用注入的共享内核完成真实计算"""

    def test_executes_worker_with_embedded_kernel_modules(self) -> None:
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
            output_path = (
                "/analyses/sales/sessions/attribution/region/contribution.json"
            )
            payload = {
                "operation": "calculate_contribution",
                "data_path": "/analyses/sales/shared/source.csv",
                "outputs": {"result": output_path},
                "parameters": {
                    "dimension_column": "region",
                    "metric_column": "sales",
                    "period_column": "period",
                    "baseline_value": "before",
                    "comparison_value": "after",
                    "top_n": 10,
                },
                "max_input_bytes": 1024 * 1024,
                "max_rows": 100,
            }
            encoded_payload = base64.urlsafe_b64encode(
                json.dumps(payload).encode("utf-8")
            ).decode("ascii")

            completed = subprocess.run(
                [sys.executable, "-c", _WORKER_BOOTSTRAP, encoded_payload],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            response = json.loads(completed.stdout)
            self.assertEqual(response["result"]["total_change"], -40.0)
            artifact = json.loads((root / output_path.lstrip("/")).read_text())
            self.assertEqual(artifact["total_change"], -40.0)


if __name__ == "__main__":
    unittest.main()
