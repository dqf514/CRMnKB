"""同步面板（OneDrive 式弹出窗口）+ UI 线程管理。

架构：主线程跑 tkinter（本模块），托盘在后台线程通过队列投递命令
（打开面板/设置/退出），面板每 300ms 从 TransferTracker 快照刷新列表。
"""
import logging
import queue
import tkinter as tk
from pathlib import Path
from tkinter import ttk

logger = logging.getLogger("sync.ui")

_DIR_STYLE = {
    "up": ("↑", "#059669", "上传"),
    "down": ("↓", "#2563eb", "下载"),
    "del": ("🗑", "#6b7280", "删除"),
    "move": ("➜", "#7c3aed", "移动"),
}


def _fmt_size(n: int) -> str:
    n = n or 0
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


class Flyout:
    """OneDrive 式同步面板：无边框右下角弹出，活动列表实时滚动刷新。"""

    W, H = 372, 460

    def __init__(self, root: tk.Tk, engine, on_open_folder, on_open_web, on_settings):
        self.root = root
        self.engine = engine
        self.on_open_folder = on_open_folder
        self.on_open_web = on_open_web
        self.on_settings = on_settings
        self.win: tk.Toplevel | None = None
        self._rows: dict[int, dict] = {}  # item_id -> 行控件
        self._photo = None

    # ---------- 窗口骨架 ----------
    def _build(self):
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{self.W}x{self.H}+{sw - self.W - 12}+{sh - self.H - 60}")

        outer = tk.Frame(win, bg="#ffffff", highlightbackground="#d0d5dd",
                         highlightthickness=1)
        outer.pack(fill="both", expand=True)

        # 头部：品牌 + 状态
        head = tk.Frame(outer, bg="#ffffff")
        head.pack(fill="x", padx=14, pady=(12, 4))
        self.brand_lbl = tk.Label(head, text=self._brand(), bg="#ffffff",
                                  font=("Microsoft YaHei UI", 12, "bold"))
        self.brand_lbl.pack(side="left")
        self.status_lbl = tk.Label(head, text="", bg="#ffffff", fg="#667085",
                                   font=("Microsoft YaHei UI", 9))
        self.status_lbl.pack(side="right")

        ttk.Separator(outer, orient="horizontal").pack(fill="x", padx=10)

        # 列表（canvas + 内部 frame + 滚动条）
        body = tk.Frame(outer, bg="#ffffff")
        body.pack(fill="both", expand=True, padx=6, pady=4)
        self.canvas = tk.Canvas(body, bg="#ffffff", highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.list_frame = tk.Frame(self.canvas, bg="#ffffff")
        self.list_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw", width=self.W - 40)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        # 底部按钮
        foot = tk.Frame(outer, bg="#f8fafc")
        foot.pack(fill="x", side="bottom")
        ttk.Separator(outer, orient="horizontal").pack(fill="x", padx=10, side="bottom")
        for text, cmd in (("打开文件夹", self.on_open_folder),
                          ("网页版", self.on_open_web),
                          ("设置", self.on_settings)):
            ttk.Button(foot, text=text, command=cmd, width=9).pack(side="left", padx=8, pady=8)
        self.pause_btn = ttk.Button(foot, text="暂停", command=self._toggle_pause, width=7)
        self.pause_btn.pack(side="right", padx=8, pady=8)

        win.bind("<FocusOut>", self._on_focus_out)
        self._refresh()

    def _on_focus_out(self, event):
        """焦点在窗口内子控件间移动也会触发 FocusOut（tkinter 事件会上冒）——
        直接 hide 会把按钮在按下瞬间销毁、点击失效。延迟确认焦点真正离开窗口才收起。"""
        self.root.after(120, self._hide_if_unfocused)

    def _hide_if_unfocused(self):
        if not self.win or not self.win.winfo_exists():
            return
        focused = self.win.focus_get()
        # 焦点不在面板内（None=别的应用；其他=本应用其他窗口）才收起
        if focused is None or not str(focused).startswith(str(self.win)):
            self.hide()

    def _brand(self) -> str:
        return f"{getattr(self.engine, 'brand_name', '') or '知识库'}同步"

    def _on_wheel(self, event):
        if self.win and self.win.winfo_exists():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _toggle_pause(self):
        if self.engine.paused:
            self.engine.resume()
        else:
            self.engine.pause()

    # ---------- 行渲染（按 item id 增量更新，避免闪烁） ----------
    def _make_row(self, item: dict) -> dict:
        f = tk.Frame(self.list_frame, bg="#ffffff")
        top = tk.Frame(f, bg="#ffffff")
        top.pack(fill="x", pady=(4, 0))
        arrow, color, _ = _DIR_STYLE.get(item["direction"], ("•", "#667085", ""))
        arrow_lbl = tk.Label(top, text=arrow, fg=color, bg="#ffffff",
                             font=("Microsoft YaHei UI", 11, "bold"), width=2)
        arrow_lbl.pack(side="left")
        name_lbl = tk.Label(top, text=item["name"], bg="#ffffff", anchor="w",
                            font=("Microsoft YaHei UI", 9))
        name_lbl.pack(side="left", fill="x", expand=True)
        info_lbl = tk.Label(top, text="", bg="#ffffff", fg="#667085",
                            font=("Microsoft YaHei UI", 8))
        info_lbl.pack(side="right")
        bar = ttk.Progressbar(f, mode="determinate", length=330)
        widgets = {"frame": f, "arrow": arrow_lbl, "name": name_lbl,
                   "info": info_lbl, "bar": bar}
        self._update_row(widgets, item)
        return widgets

    def _update_row(self, w: dict, item: dict):
        arrow, color, verb = _DIR_STYLE.get(item["direction"], ("•", "#667085", ""))
        w["arrow"].config(text=arrow, fg=color)
        name = item["name"]
        w["name"].config(text=name if len(name) <= 28 else name[:26] + "…")
        if item["status"] == "active":
            total, done = item["total"] or 0, item["transferred"]
            pct = min(100, int(done * 100 / total)) if total else 0
            w["info"].config(text=f"{_fmt_size(done)} / {_fmt_size(total)}")
            if not w["bar"].winfo_ismapped():
                w["bar"].pack(fill="x", padx=(26, 4), pady=(1, 4))
            w["bar"]["value"] = pct
        else:
            if w["bar"].winfo_ismapped():
                w["bar"].pack_forget()
            if item["status"] == "done":
                w["info"].config(text=f"{verb}完成 · {_fmt_size(item['total'])}")
            elif item["status"] == "error":
                w["info"].config(text="失败")
            else:
                w["info"].config(text="已跳过")

    def _refresh(self):
        if not self.win or not self.win.winfo_exists():
            return
        snap = self.engine.activity.snapshot()
        # 状态行
        st = self.engine.status()
        n = snap["active_count"]
        self.status_lbl.config(text=f"正在同步 {n} 个文件…" if n else st["text"])
        self.pause_btn.config(text="继续" if self.engine.paused else "暂停")
        # 行增量更新
        seen = set()
        for item in snap["items"]:
            seen.add(item["id"])
            if item["id"] not in self._rows:
                self._rows[item["id"]] = self._make_row(item)
                self._rows[item["id"]]["frame"].pack(fill="x")
                self.canvas.yview_moveto(1.0)  # 新条目滚动到底（OneDrive 滚动体感）
            else:
                self._update_row(self._rows[item["id"]], item)
        for tid in [t for t in self._rows if t not in seen]:
            self._rows[tid]["frame"].destroy()
            del self._rows[tid]
        if not snap["items"]:
            tk.Label(self.list_frame, text="暂无同步活动", bg="#ffffff",
                     fg="#98a2b3").pack(pady=30)
        self.win.after(300, self._refresh)

    # ---------- 显隐 ----------
    def show(self):
        if self.win and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return
        self._rows = {}
        self._build()
        self.win.focus_force()

    def hide(self):
        if self.win and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self._rows = {}

    def toggle(self):
        if self.win and self.win.winfo_exists():
            self.hide()
        else:
            self.show()


class UIApp:
    """UI 线程（主线程）：持有 tk root，轮询托盘投递的命令。"""

    def __init__(self, engine, icon_png: Path):
        self.engine = engine
        self.icon_png = icon_png
        self.queue: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.flyout = Flyout(
            self.root, engine,
            on_open_folder=lambda: self.post("open_folder"),
            on_open_web=lambda: self.post("open_web"),
            on_settings=lambda: self.post("settings"),
        )
        self.on_open_folder = None  # main 注入
        self.on_open_web = None
        self.on_quit = None

    def post(self, cmd: str) -> None:
        self.queue.put(cmd)

    def _handle(self, cmd: str) -> None:
        if cmd == "flyout":
            self.flyout.toggle()
        elif cmd == "settings":
            self._settings_flow()
        elif cmd == "open_folder" and self.on_open_folder:
            self.on_open_folder()
        elif cmd == "open_web" and self.on_open_web:
            self.on_open_web()
        elif cmd == "quit" and self.on_quit:
            self.on_quit()

    def _settings_flow(self):
        from .gui import open_settings, save_config
        from .main import restart_app

        try:
            new_cfg = open_settings(self.engine.cfg, self.icon_png,
                                    getattr(self.engine, "brand_name", "") or "知识库",
                                    parent=self.root)
        except Exception:
            logger.exception("设置面板异常")
            return
        if not new_cfg:
            return
        if new_cfg != self.engine.cfg:
            save_config(new_cfg)
            self.engine.notices.append("设置已保存，正在重启同步…")
            self.engine.stop()
            self.root.after(1200, restart_app)

    def _poll(self):
        try:
            while True:
                self._handle(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def run(self):
        self.root.after(100, self._poll)
        self.root.mainloop()
