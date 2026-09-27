"""自动备份 / 恢复。

备份：pg_dump（--clean --if-exists 便于恢复）+ 上传/品牌目录 tar.gz + manifest。
保留最近 N 份。恢复：先还原上传目录，再导入 SQL（会先删表再重建，破坏性操作）。
"""
import asyncio
import gzip
import json
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

RETENTION = 10  # 保留最近备份份数
CONTAINER = settings.PG_DOCKER_CONTAINER


def _db_creds() -> dict:
    u = urlparse(settings.DATABASE_URL.replace("+asyncpg", ""))
    return {
        "user": u.username,
        "password": u.password,
        "db": u.path.lstrip("/"),
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
    }


def _pg_argv(base: list[str], creds: dict | None = None) -> list[str]:
    """拼接可执行命令：优先 docker exec（同机容器 PG），否则本地 pg_dump/psql 走网络（远程 PG 服务器）。"""
    if CONTAINER:
        return ["docker", "exec", "-i", CONTAINER, *base]
    creds = creds or _db_creds()
    # 本地二进制模式：必须带主机/端口才能连远程 PG
    return [*base[:1], "-h", creds["host"], "-p", str(creds["port"]), *base[1:]]


async def _run(cmd: list[str], input: bytes | None = None, env: dict | None = None) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input is not None else None,
            env=env,
        )
        out, err = await proc.communicate(input)
        if proc.returncode != 0:
            raise RuntimeError(err.decode(errors="replace")[:400])
        return out
    except FileNotFoundError:
        raise RuntimeError("未找到 docker / pg_dump，请检查部署环境")


def _pg_env(creds: dict) -> dict | None:
    """本地二进制模式（远程 PG）：密码经环境变量传递，不进命令行。"""
    if CONTAINER:
        return None
    import os

    return {**os.environ, "PGPASSWORD": creds["password"] or ""}


def _manifest_path(d: Path) -> Path:
    return d / "manifest.json"


async def run_backup() -> str:
    """执行一次完整备份，返回备份名（时间戳目录名）。"""
    creds = _db_creds()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = settings.backup_path / ts
    d.mkdir(parents=True, exist_ok=True)
    try:
        dump = await _run(
            _pg_argv(["pg_dump", "-U", creds["user"], "-d", creds["db"], "--clean", "--if-exists"], creds),
            env=_pg_env(creds),
        )
        with gzip.open(d / "db.sql.gz", "wb") as f:
            f.write(dump)
        with tarfile.open(d / "uploads.tar.gz", "w:gz") as tar:
            for src in (settings.upload_path, settings.brand_path):
                if src.exists():
                    tar.add(src, arcname=src.name)
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "ts": ts,
                    "created_at": datetime.now().isoformat(),
                    "db_bytes": len(dump),
                    "uploads_bytes": (d / "uploads.tar.gz").stat().st_size,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    await _prune()
    return ts


async def list_backups() -> list[dict]:
    """列出已有备份（按时间倒序）。"""
    if not settings.backup_path.exists():
        return []
    items = []
    for d in sorted(settings.backup_path.iterdir(), reverse=True):
        if not d.is_dir() or not _manifest_path(d).exists():
            continue
        try:
            meta = json.loads(_manifest_path(d).read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append(
            {
                "name": d.name,
                "created_at": meta.get("created_at", d.name),
                "db_bytes": meta.get("db_bytes", 0),
                "uploads_bytes": meta.get("uploads_bytes", 0),
            }
        )
    return items


async def restore_backup(name: str) -> None:
    """从备份恢复（破坏性：覆盖当前数据库与上传目录）。"""
    d = settings.backup_path / name
    if not d.is_dir() or not _manifest_path(d).exists():
        raise RuntimeError("备份不存在")
    # 1) 还原上传/品牌目录
    if (d / "uploads.tar.gz").exists():
        shutil.rmtree(settings.upload_path, ignore_errors=True)
        shutil.rmtree(settings.brand_path, ignore_errors=True)
        with tarfile.open(d / "uploads.tar.gz", "r:gz") as tar:
            tar.extractall(settings.backup_path.parent, filter="data")  # 还原 data/uploads、data/brand
    # 2) 还原数据库（--clean 先删表再建）
    creds = _db_creds()
    data = gzip.open(d / "db.sql.gz", "rb").read()
    await _run(
        _pg_argv(["psql", "-U", creds["user"], "-d", creds["db"]], creds),
        input=data,
        env=_pg_env(creds),
    )


async def _prune() -> None:
    """只保留最近 RETENTION 份备份。"""
    if not settings.backup_path.exists():
        return
    dirs = sorted(
        (x for x in settings.backup_path.iterdir() if x.is_dir() and _manifest_path(x).exists()),
        reverse=True,
    )
    for old in dirs[RETENTION:]:
        shutil.rmtree(old, ignore_errors=True)
