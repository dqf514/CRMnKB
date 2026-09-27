"""同步状态（SQLite）：本地路径 ↔ file_id、双方哈希、下行游标。"""
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,          -- 相对路径（posix 风格，a/b/c.pdf）
    file_id INTEGER NOT NULL,
    last_synced_hash TEXT NOT NULL  -- 上次双向一致时的内容哈希
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 状态仅由同步引擎线程读写（托盘只读引擎内存状态），关闭线程检查即可
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ---- files ----
    def get(self, path: str) -> dict | None:
        row = self.conn.execute(
            "SELECT path, file_id, last_synced_hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        if not row:
            return None
        return {"path": row[0], "file_id": row[1], "last_synced_hash": row[2]}

    def get_by_id(self, file_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT path, file_id, last_synced_hash FROM files WHERE file_id = ?", (file_id,)
        ).fetchone()
        if not row:
            return None
        return {"path": row[0], "file_id": row[1], "last_synced_hash": row[2]}

    def upsert(self, path: str, file_id: int, last_synced_hash: str) -> None:
        self.conn.execute(
            "INSERT INTO files(path, file_id, last_synced_hash) VALUES(?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET file_id=excluded.file_id,"
            " last_synced_hash=excluded.last_synced_hash",
            (path, file_id, last_synced_hash),
        )
        self.conn.commit()

    def remove(self, path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self.conn.commit()

    def clear_files(self) -> None:
        """清空全部同步状态（切换账号时调用，下次启动重新全量对齐）。"""
        self.conn.execute("DELETE FROM files")
        self.conn.commit()

    def all_paths(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT path FROM files").fetchall()}

    # ---- meta ----
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
