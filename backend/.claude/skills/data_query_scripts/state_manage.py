import json
from pathlib import Path

import aiosqlite
from filelock import FileLock

# ==================== SQLite ====================
SQLITE_DB = Path(__file__).parent.parent / "session" / "session.db"


async def init_sqlite():
    """初始化 SQLite 数据库表"""
    SQLITE_DB.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(SQLITE_DB) as db:
        await db.execute("PRAGMA journal_mode=WAL")  # 开启 WAL 模式提高并发性能
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()


async def write_state_to_sqlite(data: dict, session_id: str):
    """保存状态到 SQLite 数据库"""
    await init_sqlite()
    async with aiosqlite.connect(SQLITE_DB) as db:
        # 读取先前状态
        async with db.execute(
            "SELECT state FROM session_state WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
        state = {**(json.loads(row[0]) if row else {}), **data}
        # 写入更新状态
        await db.execute(
            """
            INSERT INTO session_state (session_id, state, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET
                state = excluded.state,
                updated_at = datetime('now')
            """,
            (session_id, json.dumps(state, ensure_ascii=False)),
        )
        await db.commit()
    print(f"{list(data.keys())} saved to SQLite: session_id={session_id}")


async def read_state_from_sqlite(session_id: str):
    """从 SQLite 数据库读取状态"""
    if not SQLITE_DB.exists():
        return {}
    async with aiosqlite.connect(SQLITE_DB) as db:
        async with db.execute(
            "SELECT state FROM session_state WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else {}


# ==================== JSON 文件 ====================
SESSION_DIR = Path(__file__).parent.parent / "session"
LOCK_TIMEOUT = 5


async def write_state_to_json(data: dict, session_id: str):
    """保存状态到JSON文件"""
    STATE_FILE = SESSION_DIR / session_id / "state.json"
    LOCK_FILE = SESSION_DIR / session_id / "state.json.lock"
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(LOCK_FILE, timeout=LOCK_TIMEOUT):
        state = (
            {**json.loads(STATE_FILE.read_text(encoding="utf-8")), **data}
            if STATE_FILE.exists()
            else data
        )
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"{list(data.keys())} saved to: {STATE_FILE}")


async def read_state_from_json(session_id: str):
    """从JSON文件读取状态"""
    STATE_FILE = SESSION_DIR / session_id / "state.json"
    LOCK_FILE = SESSION_DIR / session_id / "state.json.lock"
    with FileLock(LOCK_FILE, timeout=LOCK_TIMEOUT):
        state = (
            json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if STATE_FILE.exists()
            else {}
        )
    return state


async def write_state(data: dict, session_id: str = "default"):
    await write_state_to_json(data, session_id)


async def read_state(session_id: str = "default"):
    return await read_state_from_json(session_id)
