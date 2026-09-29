"""随应用加载的静态提示词与技能资源路径。"""

from pathlib import Path

from app.shared.contracts.analysis import AGENT_TYPES

ASSISTANT_RESOURCES_DIR = (
    Path(__file__).resolve().parents[3] / "resources" / "assistant"
)

SYSTEM_PROMPTS = {
    role: (ASSISTANT_RESOURCES_DIR / role / "prompt.md")
    .read_text(encoding="utf-8")
    .strip()
    for role in ("planner", *AGENT_TYPES)
}
TITLE_PROMPT = (ASSISTANT_RESOURCES_DIR / "title.md").read_text(encoding="utf-8")
