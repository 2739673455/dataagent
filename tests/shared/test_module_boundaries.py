"""模块化单体的依赖方向、公开契约和关系模型归属。"""

import ast
from pathlib import Path

from app.assistant.models.base import AssistantBase
from app.assistant.models.recall import SemanticRecallSnapshot
from app.identity.models.base import AuthBase
from app.metadata.models.base import MetaBase
from app.query.models.base import QueryBase
from app.query.models.execution import QueryExecution
from app.query.models.experience import QueryExperience

_APP = Path(__file__).resolve().parents[2] / "app"
_DEPENDENCIES = {
    "shared": set(),
    "identity": {"shared"},
    "metadata": {"identity", "shared"},
    "query": {"identity", "metadata", "sandbox", "shared"},
    "assistant": {"identity", "metadata", "query", "sandbox", "shared"},
    "sandbox": {"shared"},
    "workflows": {"assistant", "identity", "metadata", "query", "sandbox", "shared"},
}
_PUBLIC = {
    domain: {"", "contracts", "errors"}
    for domain in _DEPENDENCIES
    if domain != "shared"
}
_PUBLIC_EXPORTS = {
    f"app.{domain}": set(
        ast.literal_eval(
            next(
                node.value
                for node in ast.parse((_APP / domain / "__init__.py").read_text()).body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                )
            )
        )
    )
    for domain in _PUBLIC
}


def _imports(tree, path):
    """解析绝对和相对导入，并核对根包是否明确公开了该能力。"""
    package = ["app", *path.relative_to(_APP).parts[:-1]]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                prefix = package[: len(package) - node.level + 1]
                module = ".".join([*prefix, *module.split(".")]).rstrip(".")
            if module in _PUBLIC_EXPORTS:
                for alias in node.names:
                    yield (
                        node.lineno,
                        (
                            module
                            if alias.name in _PUBLIC_EXPORTS[module]
                            else f"{module}.{alias.name}"
                        ),
                    )
            elif module == "app" or module == "app.shared":
                for alias in node.names:
                    yield node.lineno, f"{module}.{alias.name}"
            else:
                yield node.lineno, module


def test_modules_only_use_public_capabilities_in_the_allowed_direction():
    violations = []
    for domain, dependencies in _DEPENDENCIES.items():
        for path in (_APP / domain).rglob("*.py"):
            for line, module in _imports(ast.parse(path.read_text()), path):
                parts = module.split(".")
                if len(parts) < 2 or parts[0] != "app":
                    continue
                target = parts[1]
                if target == domain:
                    continue
                # HTTP/worker entrypoints wire management workflows into the host.
                composition = "api" in path.parts or path.name == "tasks.py"
                if composition and module == "app.dependencies":
                    continue
                allowed = target in dependencies or (
                    composition
                    and domain in {"identity", "metadata"}
                    and target == "workflows"
                )
                public = target == "shared" or ".".join(parts[2:]) in _PUBLIC.get(
                    target, set()
                )
                if not allowed or not public:
                    violations.append(
                        f"{path.relative_to(_APP)}:{line} imports {module}"
                    )
    assert not violations, "\n".join(violations)


def test_public_contracts_do_not_import_storage_or_application_implementations():
    for domain in ("assistant", "identity", "metadata", "query", "sandbox"):
        file = _APP / domain / "contracts.py"
        paths = (
            [file]
            if file.is_file()
            else list((_APP / domain / "contracts").rglob("*.py"))
        )
        for path in paths:
            for _, module in _imports(ast.parse(path.read_text()), path):
                assert module.split(".")[0] not in {
                    "sqlalchemy",
                    "fastapi",
                    "elasticsearch",
                    "langchain_core",
                    "langgraph",
                }
                assert not any(
                    part in {"models", "repositories", "services", "application"}
                    for part in module.split(".")
                )
                parts = module.split(".")
                if parts[0] == "app" and parts[1] != "shared":
                    assert len(parts) >= 3 and parts[2] in {"contracts", "errors"}, (
                        path,
                        module,
                    )


def test_http_routes_do_not_access_storage_or_internal_services():
    for path in _APP.glob("*/api/**/router.py"):
        tree = ast.parse(path.read_text())
        for _, module in _imports(tree, path):
            assert not {
                "repositories",
                "services",
                "service",
                "management",
                "runtime",
            }.intersection(module.split(".")), path
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "session"
            for node in ast.walk(tree)
        ), path


def test_state_reads_do_not_depend_on_runtime_creation_or_execution():
    path = _APP / "assistant/sessions/state_reader.py"
    for _, module in _imports(ast.parse(path.read_text()), path):
        assert module not in {
            "app.assistant.agents.runtime",
            "app.assistant.execution.runtime_cache",
            "app.assistant.execution.delegation",
        }


def test_query_does_not_depend_on_agent_identity_or_checkpoint_details():
    for path in (_APP / "query").rglob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(
            isinstance(node, ast.Name)
            and node.id == "AgentSessionKey"
            or isinstance(node, ast.Attribute)
            and node.attr == "checkpoint_ns"
            for node in ast.walk(tree)
        ), path


def test_conversation_snapshots_and_query_models_have_their_own_registries():
    assert SemanticRecallSnapshot.metadata is AssistantBase.metadata
    assert SemanticRecallSnapshot.__tablename__ not in MetaBase.metadata.tables
    assert QueryExecution.metadata is QueryBase.metadata
    assert QueryExperience.metadata is QueryBase.metadata
    assert set(QueryBase.metadata.tables) == {
        "query_executions",
        "query_experiences",
        "query_experience_assets",
    }
    assert not set(QueryBase.metadata.tables).intersection(MetaBase.metadata.tables)


def test_business_model_registries_are_owned_by_their_modules():
    """模块保留独立注册表，即使使用同一个物理数据库。"""
    bases = {
        "identity": AuthBase,
        "metadata": MetaBase,
        "query": QueryBase,
        "assistant": AssistantBase,
    }
    assert len({id(base.metadata) for base in bases.values()}) == len(bases)
    for domain, base in bases.items():
        assert base.__module__ == f"app.{domain}.models.base"
        assert base.registry.mappers
        for mapper in base.registry.mappers:
            assert mapper.class_.__module__.startswith(f"app.{domain}.models.")
