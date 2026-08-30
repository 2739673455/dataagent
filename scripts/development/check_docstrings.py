"""检查运行时代码中的类和函数说明"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "main.py",
)
DEFINITION_TYPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _python_files(roots: Iterable[Path]) -> Iterable[Path]:
    """按稳定顺序遍历检查范围内的 Python 文件"""
    for root in roots:
        if root.is_file():
            yield root
            continue
        yield from sorted(root.rglob("*.py"))


def _missing_docstrings(path: Path) -> Iterable[tuple[int, str, str]]:
    """列出文件内缺少 docstring 的类和函数"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, DEFINITION_TYPES) and ast.get_docstring(node) is None:
            kind = "类" if isinstance(node, ast.ClassDef) else "函数"
            yield node.lineno, kind, node.name


def main() -> int:
    """检查运行时代码并输出全部说明缺口"""
    missing: list[tuple[Path, int, str, str]] = []
    for path in _python_files(SOURCE_ROOTS):
        missing.extend(
            (path, line, kind, name)
            for line, kind, name in _missing_docstrings(path)
        )
    if not missing:
        print("类和函数说明检查通过")
        return 0

    for path, line, kind, name in missing:
        relative_path = path.relative_to(PROJECT_ROOT)
        print(f"{relative_path}:{line}: {kind} {name} 缺少 docstring")
    print(f"共发现 {len(missing)} 处说明缺口")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
