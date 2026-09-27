"""客户端自动更新：版本比对（sha256）→ 下载新包 → bat 替换重启。

仅 PyInstaller 冻结（exe）模式生效；源码运行跳过。
"""
import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from .api import Api, ApiError

logger = logging.getLogger("sync.updater")


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def current_exe() -> Path | None:
    return Path(sys.executable).resolve() if is_frozen() else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def check_for_update(api: Api) -> dict | None:
    """返回 {sha256, size} 若有新版本；无更新/未发布/非冻结返回 None。"""
    exe = current_exe()
    if exe is None:
        return None
    try:
        remote = api.client.get(f"{api.base_url}/api/v1/sync-app/version")
        if remote.status_code == 404:
            return None
        remote.raise_for_status()
        info = remote.json()
    except Exception as exc:
        logger.debug("检查更新失败: %s", exc)
        return None
    if info.get("sha256") and info["sha256"] != _sha256(exe):
        return info
    return None


def download_update(api: Api) -> Path | None:
    """下载新 exe 到 <exe>.new，返回路径；失败 None。"""
    exe = current_exe()
    if exe is None:
        return None
    tmp = exe.with_suffix(".new")
    try:
        with api.client.stream("GET", f"{api.base_url}/api/v1/sync-app/download") as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
        return tmp
    except Exception as exc:
        logger.warning("下载更新失败: %s", exc)
        tmp.unlink(missing_ok=True)
        return None


def apply_and_restart(new_exe: Path) -> None:
    """生成 update.bat：等待本进程退出 → 替换 exe → 重启 → 自删。随后立即退出本进程。"""
    exe = current_exe()
    bat = exe.with_name("sync_update.bat")
    bat.write_text(
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'move /y "{new_exe}" "{exe}" >nul\r\n'
        f'start "" "{exe}"\r\n'
        'del "%~f0"\r\n',
        encoding="gbk",  # cmd 默认代码页
    )
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    os._exit(0)


def auto_update(api: Api) -> bool:
    """检查→下载→替换重启。有更新并启动替换流程返回 True（进程将退出）。"""
    info = check_for_update(api)
    if not info:
        return False
    logger.info("发现新版本（%s bytes），开始自动更新…", info.get("size"))
    new_exe = download_update(api)
    if not new_exe:
        return False
    if _sha256(new_exe) != info["sha256"]:
        logger.warning("更新包校验失败，放弃更新")
        new_exe.unlink(missing_ok=True)
        return False
    logger.info("更新包就绪，重启应用完成更新")
    apply_and_restart(new_exe)
    return True  # 不会执行到这里
