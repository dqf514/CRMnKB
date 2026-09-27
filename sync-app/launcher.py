"""PyInstaller 打包入口（相对导入的包需经 launcher 启动）。"""
import sys

from sync_app.main import main

if __name__ == "__main__":
    sys.exit(main())
