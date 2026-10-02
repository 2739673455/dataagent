"""模块化单体的依赖方向、公开契约和关系模型归属。"""

import ast
from pathlib import Path

from app.assistant.models.recall import SemanticRecallSnapshot
from app.query.models.execution import QueryExecution
from app.query.models.experience import QueryExperience
from app.shared.database.base import AssistantBase, MetaBase, QueryBase

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
    domain: {"application", "contracts", "errors"}
    for domain in _DEPENDENCIES
    if domain != "shared"
}
_APPLICATION_EXPORTS = {
    f"app.{domain}.application": set(
        ast.literal_eval(
            next(
                node.value
                for node in ast.parse(
                    (_APP / domain / "application" / "__init__.py").read_text()
                ).body
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


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in _APPLICATION_EXPORTS:
                for alias in node.names:
                    yield (
                        node.lineno,
                        (
                            node.module
                            if alias.name in _APPLICATION_EXPORTS[node.module]
                            else f"{node.module}.{alias.name}"
                        ),
                    )
            # Imports from a domain package name an exported submodule.
            elif node.module in {f"app.{domain}" for domain in _DEPENDENCIES}:
                for alias in node.names:
                    yield node.lineno, f"{node.module}.{alias.name}"
            else:
                yield node.lineno, node.module


def test_modules_only_use_public_capabilities_in_the_allowed_direction():
    violations = []
    for domain, dependencies in _DEPENDENCIES.items():
        for path in (_APP / domain).rglob("*.py"):
            for line, module in _imports(ast.parse(path.read_text())):
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
        path = _APP / domain / "contracts.py"
        for _, module in _imports(ast.parse(path.read_text())):
            assert module.split(".")[0] not in {
                "sqlalchemy",
                "fastapi",
                "elasticsearch",
            }
            assert not any(
                part in {"models", "repositories", "services", "application"}
                for part in module.split(".")
            )


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
