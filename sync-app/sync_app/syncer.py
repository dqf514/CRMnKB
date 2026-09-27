"""同步引擎：上行（本地→云端）、下行（云端→本地）、初始对齐、冲突处理。

作用域：本地同步文件夹 ⇄ 云端一个专属文件夹（config.folder_name，如「同步-PC01」）。
- scope_prefix（"同步-PC01/"）界定范围：范围内的新文件才下载；范围外不碰
- 变更按 file_id 追踪：云端移动/重命名 → 本地跟随移动；移出范围 → 本地删除；移入 → 下载
状态模型（state.db）：每个已同步路径记录 file_id + last_synced_hash（上次双向一致的哈希）。
冲突：两边同时改 → 本地版本保留为「(本机冲突 时间戳)」副本，云端版本落回原路径。
删除：本地删 → 云端软删（回收站）；云端删 → 本地删（本地有改动挪 .sync-trash）。
"""
import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .api import Api, ApiError
from .state import State
from .watcher import is_ignored

logger = logging.getLogger("sync")

_EPOCH = "2000-01-01T00:00:00"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def conflict_name(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.stem} (本机冲突 {ts}){path.suffix}")


def _prune_empty_dirs(root: Path, start: Path) -> None:
    """从 start 向上清理空目录（不越过 root）。"""
    p = start
    while p != root and p.is_dir():
        try:
            p.rmdir()  # 仅空目录可删
        except OSError:
            break
        p = p.parent


class Syncer:
    def __init__(self, api: Api, state: State, sync_dir: Path,
                 folder_id: int | None = None, scope_prefix: str = "",
                 tracker=None):
        self.api = api
        self.state = state
        self.root = sync_dir
        self.folder_id = folder_id
        self.scope_prefix = scope_prefix  # "文件夹名/" 或 ""（=整个文档库）
        self.trash_dir = sync_dir / ".sync-trash"
        self.tracker = tracker  # TransferTracker（可选，进度面板数据源）

    # ---- 进度上报 ----
    def _track(self, name: str, direction: str, total: int = 0) -> int | None:
        return self.tracker.start(name, direction, total) if self.tracker else None

    def _track_progress(self, tid, transferred: int, total: int = 0) -> None:
        if self.tracker and tid is not None:
            self.tracker.progress(tid, transferred, total or None)

    def _track_done(self, tid, error: str = "") -> None:
        if self.tracker and tid is not None:
            (self.tracker.error(tid, error) if error else self.tracker.done(tid))

    def _in_scope(self, path: str) -> bool:
        return not self.scope_prefix or path.startswith(self.scope_prefix)

    # ------------------------------------------------------------------
    # 初始对齐：先拉云端（范围内），再扫本地补传
    # ------------------------------------------------------------------
    def initial_reconcile(self) -> None:
        logger.info("初始同步：拉取云端文件清单…")
        result = self.api.changes(_EPOCH)
        items = result["items"]
        if len(items) >= 2000:
            logger.warning("云端变更超过 2000 条，初始同步可能不完整（后续轮询会补齐增量）")
        for item in items:
            if item["action"] == "modified" and self._in_scope(item["path"]):
                self._apply_remote_modified(item, initial=True)
        self.state.set_meta("cursor", result["server_time"])
        logger.info("云端同步完成，扫描本地新增…")
        for local in sorted(self.root.rglob("*")):
            if not local.is_file():
                continue
            rel = local.relative_to(self.root).as_posix()
            if is_ignored(rel):
                continue
            if self.state.get(self._scoped_rel(rel)) is None:
                self._upload_new(local, rel)
        logger.info("初始同步完成")

    # ------------------------------------------------------------------
    # 下行：轮询云端变更（全量轮询 + 作用域/按 file_id 过滤）
    # ------------------------------------------------------------------
    def poll_remote(self) -> None:
        cursor = self.state.get_meta("cursor")
        if not cursor:
            return self.initial_reconcile()
        result = self.api.changes(cursor)
        for item in result["items"]:
            if is_ignored(item["path"]):
                continue
            try:
                self._apply_remote(item)
            except Exception:
                logger.exception("应用云端变更失败: %s", item["path"])
        self.state.set_meta("cursor", result["server_time"])

    def _apply_remote(self, item: dict) -> None:
        st_by_id = self.state.get_by_id(item["id"])
        in_scope = self._in_scope(item["path"])

        if item["action"] == "deleted":
            if st_by_id:
                self._apply_remote_deleted(item["id"], st_by_id["path"])
            return

        # 已跟踪文件路径变了 = 云端移动/重命名
        if st_by_id and st_by_id["path"] != item["path"]:
            if in_scope:
                self._apply_remote_moved(item, st_by_id)
            else:
                # 移出同步范围 → 本地删除（有本地改动则挪 .sync-trash）
                logger.info("云端移出同步范围: %s", st_by_id["path"])
                self._apply_remote_deleted(item["id"], st_by_id["path"])
            return

        if in_scope:
            self._apply_remote_modified(item)
        # 范围外且未跟踪：不管

    def _apply_remote_moved(self, item: dict, st: dict) -> None:
        old_rel, new_rel = st["path"], item["path"]
        old_local = self.root / self._local_rel(old_rel)
        new_local = self.root / self._local_rel(new_rel)
        logger.info("云端移动: %s → %s", old_rel, new_rel)
        if old_local.exists():
            new_local.parent.mkdir(parents=True, exist_ok=True)
            if new_local.exists():
                new_local.unlink()
            old_local.rename(new_local)
            _prune_empty_dirs(self.root, old_local.parent)
        self.state.remove(old_rel)
        # 内容可能同时变了；本地有未同步改动时先留冲突副本
        if new_local.exists():
            local_hash = sha256_file(new_local)
            if local_hash != st["last_synced_hash"] and item.get("content_hash") != local_hash:
                conflict = conflict_name(new_local)
                new_local.rename(conflict)
                logger.warning("冲突（移动+本地修改）: 本地版本保留为 %s", conflict.name)
        if not new_local.exists() or sha256_file(new_local) != (item.get("content_hash") or ""):
            tid = self._track(Path(new_rel).name, "down", item.get("file_size") or 0)
            try:
                self.api.download(item["id"], new_local, total=item.get("file_size") or 0,
                                  on_progress=lambda d, t: self._track_progress(tid, d, t))
                self._track_done(tid)
            except Exception as exc:
                self._track_done(tid, str(exc))
                raise
        self.state.upsert(new_rel, item["id"], sha256_file(new_local))

    def _apply_remote_modified(self, item: dict, initial: bool = False) -> None:
        rel = item["path"]
        st = self.state.get(rel)
        if st and item.get("content_hash") == st["last_synced_hash"]:
            return  # 自己刚上传的，跳过回声
        local = self.root / self._local_rel(rel)
        if local.exists():
            local_hash = sha256_file(local)
            if st is None and not initial:
                conflict = conflict_name(local)
                local.rename(conflict)
                logger.warning("冲突（同名新文件）: %s → 本地保留为 %s", rel, conflict.name)
            elif st and local_hash != st["last_synced_hash"]:
                conflict = conflict_name(local)
                local.rename(conflict)
                logger.warning("冲突（双向修改）: %s → 本地版本保留为 %s", rel, conflict.name)
            elif item.get("content_hash") == local_hash:
                self.state.upsert(rel, item["id"], local_hash)
                return  # 内容已一致，只补状态
        logger.info("下载: %s", rel)
        tid = self._track(Path(rel).name, "down", item.get("file_size") or 0)
        try:
            self.api.download(item["id"], local, total=item.get("file_size") or 0,
                              on_progress=lambda d, t: self._track_progress(tid, d, t))
            self._track_done(tid)
        except Exception as exc:
            self._track_done(tid, str(exc))
            raise
        self.state.upsert(rel, item["id"], sha256_file(local))

    def _apply_remote_deleted(self, file_id: int, rel: str) -> None:
        st = self.state.get(rel)
        if st is None:
            return
        local = self.root / self._local_rel(rel)
        if local.exists():
            if sha256_file(local) == st["last_synced_hash"]:
                local.unlink()
                _prune_empty_dirs(self.root, local.parent)
                logger.info("本地删除（云端已删）: %s", rel)
            else:
                dest = self.trash_dir / datetime.now().strftime("%Y%m%d") / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(local), str(dest))
                logger.warning("云端已删但本地有改动，移入回收目录: %s → %s", rel, dest)
        tid = self._track(Path(rel).name, "del")
        self._track_done(tid)
        self.state.remove(rel)

    # ------------------------------------------------------------------
    # 上行：本地变更队列
    # ------------------------------------------------------------------
    def flush_local(self, rel_paths: list[str]) -> None:
        for rel in rel_paths:
            try:
                self._sync_one(rel)
            except ApiError as exc:
                logger.error("同步失败 %s: %s", rel, exc)
            except Exception:
                logger.exception("同步失败: %s", rel)

    def _sync_one(self, rel: str) -> None:
        local = self.root / rel
        scoped_rel = self._scoped_rel(rel)
        st = self.state.get(scoped_rel)

        # 本地已删除 → 云端软删（回收站可恢复）
        if not local.exists():
            if st and st["file_id"] > 0:
                self.api.delete_file(st["file_id"])
                logger.info("云端删除: %s", scoped_rel)
            self.state.remove(scoped_rel)
            return

        local_hash = sha256_file(local)
        if st and local_hash == st["last_synced_hash"]:
            return

        # 新文件 → 上传
        if st is None or st["file_id"] <= 0:
            self._upload_new(local, rel, local_hash)
            return

        # 已有文件被修改：先查云端是否也变了（冲突预检）
        remote = self.api.get_file(st["file_id"])
        if remote.get("content_hash") and remote["content_hash"] != st["last_synced_hash"]:
            conflict = conflict_name(local)
            local.rename(conflict)
            logger.warning("冲突（双向修改）: %s → 本地版本上传为 %s", scoped_rel, conflict.name)
            self._upload_new(conflict, conflict.relative_to(self.root).as_posix())
            logger.info("下载云端版本: %s", scoped_rel)
            self.api.download(st["file_id"], local)
            self.state.upsert(scoped_rel, st["file_id"], sha256_file(local))
            return

        logger.info("上传更新: %s", scoped_rel)
        tid = self._track(Path(scoped_rel).name, "up", local.stat().st_size)
        try:
            result = self.api.update_content(
                st["file_id"], local,
                on_progress=lambda d, t: self._track_progress(tid, d, t))
            self._track_done(tid)
        except Exception as exc:
            self._track_done(tid, str(exc))
            raise
        self.state.upsert(scoped_rel, st["file_id"], result["content_hash"])
        if result.get("reparse_docs"):
            logger.info("  → 已触发 %d 个知识库文档重新解析", result["reparse_docs"])

    def _upload_new(self, local: Path, local_rel: str, local_hash: str | None = None) -> None:
        """上传新文件：paths 用本地相对路径（folder_id 已定位到作用域文件夹，勿重复加前缀），
        状态键用云端作用域路径。"""
        scoped_rel = self._scoped_rel(local_rel)
        logger.info("上传新文件: %s", scoped_rel)
        tid = self._track(Path(scoped_rel).name, "up", local.stat().st_size)
        try:
            result = self.api.upload_new(
                local, local_rel, folder_id=self.folder_id,
                on_progress=lambda d, t: self._track_progress(tid, d, t))
        except Exception as exc:
            self._track_done(tid, str(exc))
            raise
        files = result.get("files") or []
        if not files:
            self._track_done(tid, "上传被跳过（可能超限）")
            logger.warning("上传被跳过（可能超限）: %s", scoped_rel)
            return
        self._track_done(tid)
        file_id = files[0]["id"]
        # 取服务端 hash 作为一致基准
        remote = self.api.get_file(file_id)
        self.state.upsert(scoped_rel, file_id, remote.get("content_hash") or (local_hash or sha256_file(local)))

    # ------------------------------------------------------------------
    # 路径换算：本地相对路径 ⇄ 云端作用域路径（加/去 scope_prefix）
    # ------------------------------------------------------------------
    def _scoped_rel(self, local_rel: str) -> str:
        return f"{self.scope_prefix}{local_rel}" if self.scope_prefix else local_rel

    def _local_rel(self, scoped_rel: str) -> str:
        if self.scope_prefix and scoped_rel.startswith(self.scope_prefix):
            return scoped_rel[len(self.scope_prefix):]
        return scoped_rel
