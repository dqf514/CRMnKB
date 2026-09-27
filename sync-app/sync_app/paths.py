"""路径工具：兼容源码运行与 PyInstaller 冻结（exe 同级目录存放配置与资源）。"""
import sys
from pathlib import Path


def app_dir() -> Path:
    """配置/资源所在目录：冻结时为 exe 同级目录，源码时为 sync-app 项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_dir() -> Path:
    """只读内置资源目录（PyInstaller --add-data 解包目录）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent.parent
