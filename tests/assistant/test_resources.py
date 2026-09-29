"""静态资源加载不依赖进程工作目录。"""

import runpy

from app.assistant.agents import resources


def test_prompts_load_outside_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loaded = runpy.run_path(resources.__file__)
    assert loaded["ASSISTANT_RESOURCES_DIR"].is_absolute()
    assert set(loaded["SYSTEM_PROMPTS"]) == {
        "planner",
        "explorer",
        "analyst",
        "reviewer",
    }
    assert loaded["SYSTEM_PROMPTS"] == resources.SYSTEM_PROMPTS
    assert all(loaded["SYSTEM_PROMPTS"].values())
    assert loaded["TITLE_PROMPT"] == resources.TITLE_PROMPT
    assert "生成会话标题" in loaded["TITLE_PROMPT"]
