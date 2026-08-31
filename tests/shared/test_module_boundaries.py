"""模块化单体依赖边界测试。"""

import ast
import unittest
from collections.abc import Iterable
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
APP_DIR = ROOT_DIR / "app"
BUSINESS_MODULES = {
    "app.identity",
    "app.metadata",
    "app.query",
    "app.assistant",
    "app.sandbox",
    "app.workflows",
}


def _python_files(*paths: Path) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            raise AssertionError(f"模块边界扫描路径不存在: {path}")
        if path.is_file():
            files.append(path)
        else:
            files.extend(path.rglob("*.py"))
    return sorted(files)


def _imports(path: Path) -> Iterable[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def _matches_namespace(import_name: str, namespace: str) -> bool:
    return import_name == namespace or import_name.startswith(f"{namespace}.")


class ModuleBoundaryTests(unittest.TestCase):
    def assert_no_imports(
        self,
        files: Iterable[Path],
        forbidden_namespaces: set[str],
    ) -> None:
        violations: list[str] = []
        for path in files:
            for import_name in _imports(path):
                if any(
                    _matches_namespace(import_name, namespace)
                    for namespace in forbidden_namespaces
                ):
                    relative_path = path.relative_to(ROOT_DIR)
                    violations.append(f"{relative_path}: {import_name}")
        self.assertEqual([], violations)

    def test_shared_infrastructure_does_not_depend_on_business_modules(self) -> None:
        self.assert_no_imports(
            _python_files(
                APP_DIR / "shared",
            ),
            BUSINESS_MODULES,
        )

    def test_identity_domain_does_not_depend_on_higher_modules(self) -> None:
        self.assert_no_imports(
            _python_files(
                APP_DIR / "identity" / "models",
                APP_DIR / "identity" / "errors.py",
                APP_DIR / "identity" / "repositories",
                APP_DIR / "identity" / "services",
            ),
            BUSINESS_MODULES - {"app.identity"},
        )

    def test_metadata_core_does_not_depend_on_query_or_assistant(self) -> None:
        self.assert_no_imports(
            _python_files(
                APP_DIR / "metadata" / "config.py",
                APP_DIR / "metadata" / "models",
                APP_DIR / "metadata" / "errors.py",
                APP_DIR / "metadata" / "repositories",
                APP_DIR / "metadata" / "services",
            ),
            {
                "app.query",
                "app.assistant",
                "app.sandbox",
                "app.workflows",
            },
        )

    def test_query_module_does_not_depend_on_assistant_or_workflows(self) -> None:
        self.assert_no_imports(
            _python_files(APP_DIR / "query"),
            {"app.assistant", "app.sandbox", "app.workflows"},
        )

    def test_sandbox_module_remains_a_low_level_runtime(self) -> None:
        self.assert_no_imports(
            _python_files(APP_DIR / "sandbox"),
            BUSINESS_MODULES - {"app.sandbox"},
        )


if __name__ == "__main__":
    unittest.main()
