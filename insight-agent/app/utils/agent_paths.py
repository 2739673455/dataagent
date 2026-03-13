import shutil
from pathlib import Path

# 路径常量
CURRENT_DIR = Path(__file__).parent  # utils
UP1_DIR = CURRENT_DIR.parent  # app
UP2_DIR = UP1_DIR.parent  # 项目根目录

DEEPAGENTS_ROOT = UP2_DIR / ".deepagents"
SKILLS_DIR = DEEPAGENTS_ROOT / "skills"
WORKSPACES_DIR = DEEPAGENTS_ROOT / "workspaces"


def get_workspace_dir(user_id: int, conversation_id: int) -> Path:
    """获取并确保用户会话工作区目录存在"""
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / str(conversation_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def delete_workspace_dir(user_id: int, conversation_id: int) -> None:
    """删除用户会话工作区目录，不存在时忽略"""
    workspace_dir = WORKSPACES_DIR / f"user_{user_id}" / str(conversation_id)
    shutil.rmtree(workspace_dir, ignore_errors=True)
