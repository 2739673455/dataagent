"""检查项目 Markdown 文档中的本地链接"""

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT_DIR = Path(__file__).resolve().parents[2]
_MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
_EXTERNAL_SCHEMES = {"http", "https", "mailto", "data"}


def _documentation_files() -> list[Path]:
    """返回需要检查的项目文档"""
    return [ROOT_DIR / "README.md", *sorted((ROOT_DIR / "docs").rglob("*.md"))]


def _check_document_links(paths: Iterable[Path] | None = None) -> list[str]:
    """返回全部失效本地链接"""
    broken_links: list[str] = []
    for document_path in paths or _documentation_files():
        content = document_path.read_text()
        for match in _MARKDOWN_LINK_PATTERN.finditer(content):
            raw_target = match.group(1).strip().strip("<>")
            parsed_target = urlsplit(raw_target)
            if parsed_target.scheme in _EXTERNAL_SCHEMES:
                continue
            decoded_path = unquote(parsed_target.path)
            if not decoded_path:
                continue
            target_path = (document_path.parent / decoded_path).resolve()
            if not target_path.exists():
                line_number = content.count("\n", 0, match.start()) + 1
                relative_document = document_path.relative_to(ROOT_DIR)
                broken_links.append(f"{relative_document}:{line_number}: {raw_target}")
    return broken_links


def main() -> None:
    """执行文档链接检查"""
    broken_links = _check_document_links()
    if broken_links:
        details = "\n".join(broken_links)
        raise SystemExit(f"发现失效的本地文档链接:\n{details}")
    print("文档本地链接检查通过")


if __name__ == "__main__":
    main()
