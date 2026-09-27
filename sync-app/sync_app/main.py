"""同步客户端 - 入口。

运行方式：
    python -m sync_app            # 首次启动弹设置向导，之后托盘常驻
    python -m sync_app --console  # 无 GUI 控制台模式（调试用）
"""
import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .activity import TransferTracker
from .api import Api, ApiError
from .gui import ensure_config, fetch_brand, open_settings, save_config
from .icon import ensure_icons
from .paths import app_dir, bundled_dir
from .state import State
from .syncer import Syncer
from .updater import auto_update, check_for_update, is_frozen
from .watcher import Watcher

logger = logging.getLogger("sync")

UPDATE_CHECK_INTERVAL = 6 * 3600  # 自动更新检查间隔（6 小时）


def restart_app() -> None:
    """重启自身（设置变更后生效）。"""
    if is_frozen():
        subprocess.Popen([sys.executable], close_fds=True)
    else:
        subprocess.Popen([sys.executable, "-m", "sync_app"], cwd=str(bundled_dir()), close_fds=True)
    os._exit(0)


class Engine:
    """同步主循环（后台线程）：本地监听 + 定时下行轮询 + 上行冲刷。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sync_dir = Path(cfg["sync_dir"])
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        self.api = Api(cfg["server_url"])
        self.state = State(self.sync_dir / ".sync-state" / "sync.db")
        scope_prefix = f"{cfg['folder_name']}/" if cfg.get("folder_name") else ""
        self.activity = TransferTracker()  # 进度面板/托盘动画的数据源
        self.syncer = Syncer(self.api, self.state, self.sync_dir, cfg.get("folder_id"),
                             scope_prefix, tracker=self.activity)
        self.watcher = Watcher(self.sync_dir)
        self.poll_interval = max(5, int(cfg.get("poll_interval", 30)))
        self.brand_name = "知识库"
        self.paused = False
        self._stop = threading.Event()
        self._sync_now = threading.Event()
        self._update_check = threading.Event()
        self._last_update_check = 0.0
        self._last_activity = ""
        self._online = True
        self.notices: list[str] = []  # 托盘弹通知用（托盘线程消费）

    # ---- 托盘接口 ----
    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False
        self._sync_now.set()

    def request_sync(self):
        self._sync_now.set()

    def request_update_check(self):
        self._update_check.set()

    def stop(self):
        self._stop.set()
        self._sync_now.set()
        self._update_check.set()

    def status(self) -> dict:
        if self.paused:
            return {"state": "paused", "text": "已暂停"}
        if not self._online:
            return {"state": "offline", "text": "无法连接服务器"}
        snap = self.activity.snapshot()
        if snap["active_count"]:
            return {"state": "syncing", "text": f"正在同步 {snap['active_count']} 个文件…"}
        if self._last_activity:
            return {"state": "syncing", "text": self._last_activity}
        return {"state": "idle", "text": "已是最新"}

    def web_sso_url(self) -> str:
        """「打开网页版」免登 URL：一次性 SSO code 换取登录态。"""
        try:
            code = self._with_relogin(self.api.sso_code)
            web = self.cfg["server_url"].replace(":8100", ":5173")
            return f"{web}/sso?code={code}"
        except Exception as exc:
            logger.warning("SSO 码获取失败，退回普通登录页: %s", exc)
            return self.cfg["server_url"].replace(":8100", ":5173")

    # ---- 内部 ----
    def _login(self) -> None:
        saved = self.state.get_meta("token")
        saved_user = self.state.get_meta("sync_user")
        if saved and saved_user == self.cfg["username"]:
            self.api.set_token(saved)
            return
        if saved_user and saved_user != self.cfg["username"]:
            # 切换了账号：旧账号的同步状态全部作废，重新全量对齐
            logger.info("账号从 %s 切换为 %s，重置同步状态", saved_user, self.cfg["username"])
            self.state.clear_files()
            self.state.set_meta("cursor", "")
        token = self.api.login(self.cfg["username"], self.cfg["password"])
        self.state.set_meta("token", token)
        self.state.set_meta("sync_user", self.cfg["username"])
        logger.info("已登录: %s", self.cfg["username"])

    def _with_relogin(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            if "AUTH_EXPIRED" not in str(exc):
                raise
            self.state.set_meta("token", "")
            self._login()
            return fn(*args, **kwargs)

    def _check_updates(self, manual: bool = False) -> None:
        """自动更新检查（仅冻结 exe 模式；有更新则下载并重启完成替换）。"""
        if not is_frozen():
            if manual:
                self.notices.append("开发模式（源码运行）不支持自动更新")
            return
        try:
            if manual and check_for_update(self.api) is None:
                self.notices.append("已是最新版本")
                return
            auto_update(self.api)  # 有更新时进程内直接重启替换，不返回
        except Exception:
            logger.debug("自动更新检查失败", exc_info=True)
            if manual:
                self.notices.append("检查更新失败，请稍后再试")

    def run(self) -> None:
        self._login()
        try:
            self.brand_name = self._with_relogin(self.api.brand).get("system_name") or "知识库"
        except Exception:
            pass
        self.watcher.start()
        try:
            self._with_relogin(self.syncer.poll_remote)  # 首次：初始对齐
            last_poll = time.time()
            first_update_check = time.time() + 60  # 启动 60s 后首次检查更新
            self._last_update_check = time.time()
            while not self._stop.is_set():
                if self.paused:
                    self._sync_now.wait(1.0)
                    self._sync_now.clear()
                    continue
                try:
                    # 自动更新：手动触发 / 启动 60s 后首次 / 之后每 6 小时
                    manual = self._update_check.is_set()
                    due_first = first_update_check and time.time() >= first_update_check
                    due_periodic = time.time() - self._last_update_check >= UPDATE_CHECK_INTERVAL
                    if manual or due_first or due_periodic:
                        self._update_check.clear()
                        first_update_check = None
                        self._last_update_check = time.time()
                        self._check_updates(manual=manual)
                    # 下行轮询（到点或手动触发）
                    if self._sync_now.is_set() or time.time() - last_poll >= self.poll_interval:
                        self._last_activity = "正在检查云端变更…"
                        self._with_relogin(self.syncer.poll_remote)
                        last_poll = time.time()
                        self._sync_now.clear()
                        self._online = True
                    # 上行冲刷
                    changed = self.watcher.drain()
                    if changed:
                        self._last_activity = f"正在上传 {len(changed)} 个变更…"
                        self._with_relogin(self.syncer.flush_local, changed)
                    self._last_activity = ""
                    self._online = True
                except (ApiError, ConnectionError, OSError) as exc:
                    self._online = False
                    logger.warning("网络/服务异常（稍后重试）: %s", exc)
                    time.sleep(5)
                except Exception:
                    logger.exception("同步循环异常")
                    time.sleep(5)
                self._stop.wait(1.0)
        finally:
            self.watcher.stop()
            self.state.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库同步客户端")
    parser.add_argument("--console", action="store_true", help="控制台模式（无托盘 GUI）")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # 图标：优先 exe 同级 assets（可被覆盖定制），否则内置资源
    assets = app_dir() / "assets"
    if not (assets / "icon.png").exists() and (bundled_dir() / "assets" / "icon.png").exists():
        icon_png = bundled_dir() / "assets" / "icon.png"
    else:
        icon_png, _ = ensure_icons(assets)

    cfg = ensure_config(icon_png)
    if not cfg:
        print("已取消设置，退出。")
        return 1

    engine = Engine(cfg)
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()

    if args.console:
        print(f"同步运行中（{cfg['sync_dir']}），Ctrl+C 退出")
        try:
            while t.is_alive():
                t.join(1.0)
        except KeyboardInterrupt:
            engine.stop()
            t.join(5)
        return 0

    # GUI 模式：主线程跑 tkinter（面板/设置），托盘在后台线程
    import os
    import webbrowser

    from .tray import TrayApp
    from .ui import UIApp

    ui = UIApp(engine, icon_png)
    ui.on_open_folder = lambda: os.startfile(str(engine.sync_dir))
    ui.on_open_web = lambda: webbrowser.open(engine.web_sso_url())

    def _quit():
        engine.stop()
        tray.stop()
        ui.root.after(200, ui.root.destroy)

    ui.on_quit = _quit
    tray = TrayApp(engine, icon_png, engine.sync_dir, cfg["server_url"], ui.post)
    threading.Thread(target=tray.run, daemon=True).start()
    ui.run()  # 主线程 tkinter 主循环
    engine.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
