"""系统托盘（pystray，后台线程运行）：动画图标 + 菜单。

- 同步中：双箭头 12 帧旋转动画；暂停=灰；离线=橙点角标
- 单击图标（默认项）→ 打开同步面板；菜单动作经 UI 队列投递给主线程 tkinter
"""
import logging
import threading
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from .icon import render_frames

logger = logging.getLogger("sync.tray")


def _badge(base: Image.Image, color: tuple) -> Image.Image:
    img = base.copy()
    d = ImageDraw.Draw(img)
    s = img.width
    r = int(s * 0.14)
    x0, y0 = s - r * 2 - int(s * 0.04), s - r * 2 - int(s * 0.04)
    d.ellipse([x0 - 2, y0 - 2, x0 + r * 2 + 2, y0 + r * 2 + 2], fill=(255, 255, 255, 255))
    d.ellipse([x0, y0, x0 + r * 2, y0 + r * 2], fill=color)
    return img


class TrayApp:
    def __init__(self, engine, icon_png: Path, sync_dir: Path, server_url: str, ui_post):
        self.engine = engine
        self.sync_dir = sync_dir
        self.server_url = server_url
        self.ui_post = ui_post  # UI 命令投递（flyout/settings/quit/open_folder/open_web）
        self._frames = render_frames(64, steps=12)
        self._frame_idx = 0
        self._icons = {
            "paused": self._frames[0].convert("LA").convert("RGBA"),
            "offline": _badge(self._frames[0], (249, 115, 22)),
        }
        self.icon = pystray.Icon("kb-sync", self._frames[0], "同步客户端", self._menu())
        self._stop = threading.Event()

    def _brand(self) -> str:
        return getattr(self.engine, "brand_name", None) or "知识库"

    def _title(self, text: str) -> str:
        return f"{self._brand()}同步 - {text}"

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("打开同步面板", lambda *_: self.ui_post("flyout"), default=True),
            pystray.MenuItem("打开同步文件夹", lambda *_: self.ui_post("open_folder")),
            pystray.MenuItem("打开网页版", lambda *_: self.ui_post("open_web")),
            pystray.MenuItem("立即同步", self._sync_now),
            pystray.MenuItem("检查更新", self._check_update),
            pystray.MenuItem("设置", lambda *_: self.ui_post("settings")),
            pystray.MenuItem("继续同步" if self.engine.paused else "暂停同步", self._toggle_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda *_: self.ui_post("quit")),
        )

    # ---- 菜单动作（直接操作引擎的走引擎事件，UI 类走队列） ----
    def _sync_now(self, *_):
        self.engine.request_sync()

    def _check_update(self, *_):
        self.engine.request_update_check()

    def _toggle_pause(self, icon, item):
        if self.engine.paused:
            self.engine.resume()
        else:
            self.engine.pause()
        icon.menu = self._menu()
        icon.update_menu()

    def stop(self):
        self._stop.set()
        self.icon.stop()

    # ---- 状态/动画刷新 ----
    def _refresh_loop(self):
        while not self._stop.wait(0.15):
            try:
                st = self.engine.status()
                state = st["state"]
                if state == "syncing":
                    self._frame_idx = (self._frame_idx + 1) % len(self._frames)
                    self.icon.icon = self._frames[self._frame_idx]
                elif state in self._icons:
                    self.icon.icon = self._icons[state]
                else:
                    self.icon.icon = self._frames[0]
                self.icon.title = self._title(st["text"])
                while self.engine.notices:
                    msg = self.engine.notices.pop(0)
                    try:
                        self.icon.notify(msg, self._title(""))
                    except Exception:
                        logger.info("通知: %s", msg)
            except Exception:
                logger.debug("托盘状态刷新失败", exc_info=True)

    def run(self):
        t = threading.Thread(target=self._refresh_loop, daemon=True)
        t.start()
        self.icon.run()
