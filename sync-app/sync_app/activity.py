"""传输活动追踪（线程安全）：同步面板与托盘图标的数据源。

记录每个文件的上/下载进度与状态（active/done/error/skipped），保留最近 30 条。
"""
import threading
import time
from collections import deque


class TransferTracker:
    MAX_ITEMS = 30

    def __init__(self):
        self._lock = threading.Lock()
        self._items: deque[dict] = deque(maxlen=self.MAX_ITEMS)
        self._seq = 0

    def start(self, name: str, direction: str, total: int = 0) -> int:
        """direction: up / down / del / move。返回条目 id。"""
        with self._lock:
            self._seq += 1
            tid = self._seq
            self._items.append({
                "id": tid, "name": name, "direction": direction,
                "transferred": 0, "total": total, "status": "active",
                "error": "", "ts": time.time(),
            })
            return tid

    def progress(self, tid: int, transferred: int, total: int | None = None) -> None:
        with self._lock:
            for it in self._items:
                if it["id"] == tid and it["status"] == "active":
                    it["transferred"] = transferred
                    if total:
                        it["total"] = total
                    it["ts"] = time.time()
                    return

    def done(self, tid: int) -> None:
        self._finish(tid, "done")

    def error(self, tid: int, msg: str = "") -> None:
        self._finish(tid, "error", msg)

    def _finish(self, tid: int, status: str, msg: str = "") -> None:
        with self._lock:
            for it in self._items:
                if it["id"] == tid:
                    it["status"] = status
                    it["error"] = msg
                    if status == "done" and it["total"]:
                        it["transferred"] = it["total"]
                    it["ts"] = time.time()
                    return

    def snapshot(self) -> dict:
        """面板用快照：活动项在前，其余按时间倒序。"""
        with self._lock:
            items = list(self._items)
        active = [i for i in items if i["status"] == "active"]
        recent = sorted((i for i in items if i["status"] != "active"),
                        key=lambda i: i["ts"], reverse=True)
        return {
            "items": active + recent,
            "active_count": len(active),
            "active_text": active[-1]["name"] if active else "",
        }
