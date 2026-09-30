"""加载随源码部署的 Assistant 提示词和技能资源。"""

from pathlib import Path

_RESOURCE_DIRECTORY = Path(__file__).resolve().parents[2] / "resources"
SKILLS_DIRECTORY = _RESOURCE_DIRECTORY / "skills"


def load_prompt(name: str) -> str:
    """按内部资源名读取提示词。"""
    return (
        (_RESOURCE_DIRECTORY / "prompts" / f"{name}.md")
        .read_text(encoding="utf-8")
        .strip()
    )
