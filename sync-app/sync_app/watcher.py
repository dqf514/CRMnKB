"""本地文件夹监听（watchdog）：变更事件去抖后进入上行队列。"""
import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("sync.watch")

# 忽略：同步内部目录、临时文件、Office 锁文件、隐藏文件
IGNORED_PARTS = {".sync-trash", ".sync-state"}
IGNORED_SUFFIXES = {".sync-part", ".tmp", ".part", ".crdownload", ".download"}


def is_ignored(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(p in IGNORED_PARTS or p.startswith("~$") for p in parts):
        return True
    name = Path(rel_path).name
    if name.startswith(".") or name in {"sync.db", "config.json"}:
        return True
    # 冲突副本：下行冲突时本地保留的副本仅留本地，不再回传（上行冲突的副本由引擎显式上传）
    if " (本机冲突 " in name:
        return True
    return any(name.endswith(s) for s in IGNORED_SUFFIXES)


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, pending: dict):
        self.root = root
        self.pending = pending  # {rel_path: last_event_ts}

    def _rel(self, src: str) -> str | None:
        try:
            rel = Path(src).relative_to(self.root).as_posix()
        except ValueError:
            return None
        return None if is_ignored(rel) else rel

    def _mark(self, src: str):
        rel = self._rel(src)
        if rel:
            self.pending[rel] = time.time()

    def on_created(self, event):
        if not event.is_directory:
            self._mark(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._mark(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._mark(event.src_path)

    def on_moved(self, event):
        # 移动 = 源删除 + 目标新建
        if not event.is_directory:
            self._mark(event.src_path)
            self._mark(event.dest_path)


class Watcher:
    DEBOUNCE_SEC = 2.0

    def __init__(self, root: Path):
        self.root = root
        self.pending: dict[str, float] = {}
        self._observer = Observer()
        self._observer.schedule(_Handler(root, self.pending), str(root), recursive=True)

    def start(self):
        self._observer.start()
        logger.info("开始监听本地目录: %s", self.root)

    def stop(self):
        self._observer.stop()
        self._observer.join(timeout=5)

    def drain(self) -> list[str]:
        """取出已稳定（去抖时间已过）的变更路径。"""
        now = time.time()
        ready = [p for p, ts in self.pending.items() if now - ts >= self.DEBOUNCE_SEC]
        for p in ready:
            del self.pending[p]
        return ready
