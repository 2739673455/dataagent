"""确定性分析工具的 Session 上下文和 Docker 执行边界"""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from langchain.tools import ToolRuntime

from app.agents.contracts import AgentSessionKey, AgentType, ArtifactReference
from app.clients.docker_sandbox_manager import docker_sandbox_manager
from app.conf.app_config import cfg

_MAX_ANALYSIS_INPUT_BYTES = 16 * 1024 * 1024
_MAX_ANALYSIS_REQUEST_BYTES = 256 * 1024
_ANALYSIS_TIMEOUT_SECONDS = 120
_ANALYSIS_ROOT = Path(__file__).parents[2] / "analysis"
_KERNEL_SOURCES = {
    f"app.analysis.{module_name}": (_ANALYSIS_ROOT / f"{module_name}.py").read_text(
        encoding="utf-8"
    )
    for module_name in ("attribution", "anomaly_detection", "visualization")
}
_ENCODED_KERNEL_SOURCES = base64.b64encode(
    json.dumps(_KERNEL_SOURCES, separators=(",", ":")).encode("utf-8")
).decode("ascii")
_WORKER_SOURCE = (
    Path(__file__).with_name("sandbox_analysis_worker.py").read_text(encoding="utf-8")
)
_ENCODED_WORKER_SOURCE = base64.b64encode(_WORKER_SOURCE.encode("utf-8")).decode(
    "ascii"
)
_WORKER_BOOTSTRAP = f"""
import base64 as _base64
import json as _json
import sys as _sys
import types as _types

_app_package = _types.ModuleType("app")
_app_package.__path__ = []
_analysis_package = _types.ModuleType("app.analysis")
_analysis_package.__path__ = []
_sys.modules["app"] = _app_package
_sys.modules["app.analysis"] = _analysis_package
setattr(_app_package, "analysis", _analysis_package)

_kernel_sources = _json.loads(
    _base64.b64decode({_ENCODED_KERNEL_SOURCES!r}).decode("utf-8")
)
for _module_name, _module_source in _kernel_sources.items():
    _module = _types.ModuleType(_module_name)
    _module.__package__ = "app.analysis"
    _module.__file__ = f"<{{_module_name}}>"
    _sys.modules[_module_name] = _module
    setattr(_analysis_package, _module_name.rsplit(".", 1)[-1], _module)
    exec(compile(_module_source, _module.__file__, "exec"), _module.__dict__)

exec(
    compile(
        _base64.b64decode({_ENCODED_WORKER_SOURCE!r}),
        "<sandbox_analysis_worker>",
        "exec",
    )
)
"""


@dataclass(frozen=True, slots=True)
class SpecialistToolContext:
    """从可信运行配置解析出的专业 Session 身份"""

    key: AgentSessionKey

    def artifact_path(self, stem: str, suffix: str) -> str:
        """生成当前 Session 内不可变的产物路径"""
        version = uuid4().hex[:12]
        return (
            f"/analyses/{self.key.analysis_id}/sessions/{self.key.agent_type}/"
            f"{self.key.session_id}/{stem}_{version}.{suffix}"
        )


def get_specialist_tool_context(
    runtime: ToolRuntime,
    expected_agent_type: AgentType,
) -> SpecialistToolContext:
    """读取并校验专业 Agent 的运行身份"""
    configurable = runtime.config.get("configurable", {})
    user_id = configurable.get("user_id")
    conversation_id = configurable.get("conversation_id")
    analysis_id = configurable.get("analysis_id")
    agent_type = configurable.get("agent_type")
    session_id = configurable.get("session_id")
    if (
        not isinstance(user_id, int)
        or not isinstance(conversation_id, str)
        or not isinstance(analysis_id, str)
        or agent_type != expected_agent_type
        or not isinstance(session_id, str)
    ):
        raise TypeError("specialist session context not found in config")
    return SpecialistToolContext(
        AgentSessionKey(
            user_id=user_id,
            conversation_id=UUID(conversation_id),
            analysis_id=analysis_id,
            agent_type=expected_agent_type,
            session_id=session_id,
        )
    )


async def run_sandbox_analysis(
    context: SpecialistToolContext,
    *,
    operation: str,
    data_path: str,
    outputs: dict[str, str],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """在资源受限的会话 Docker 容器内读取数据并生成产物"""
    ArtifactReference(path=data_path)
    analysis_prefix = f"/analyses/{context.key.analysis_id}/"
    if not data_path.startswith(analysis_prefix):
        raise ValueError("analysis input must belong to the current analysis")
    output_prefix = (
        f"/analyses/{context.key.analysis_id}/sessions/{context.key.agent_type}/"
        f"{context.key.session_id}/"
    )
    for path in outputs.values():
        ArtifactReference(path=path)
        if not path.startswith(output_prefix):
            raise ValueError("analysis output must belong to the current session")

    payload = json.dumps(
        {
            "operation": operation,
            "data_path": data_path,
            "outputs": outputs,
            "parameters": parameters,
            "max_input_bytes": _MAX_ANALYSIS_INPUT_BYTES,
            "max_rows": cfg.query.max_rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_ANALYSIS_REQUEST_BYTES:
        raise ValueError("analysis request is too large")
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii")
    command = (
        f"python -c {shlex.quote(_WORKER_BOOTSTRAP)} {shlex.quote(encoded_payload)}"
    )
    backend = await docker_sandbox_manager.get_backend(
        context.key.user_id,
        context.key.conversation_id,
    )
    execution = await backend.aexecute(
        command,
        timeout=_ANALYSIS_TIMEOUT_SECONDS,
    )
    if execution.exit_code != 0:
        raise RuntimeError(execution.output.strip() or "sandbox analysis failed")
    try:
        result = json.loads(execution.output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("sandbox analysis returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise TypeError("sandbox analysis returned an invalid result")
    return result
