"""设置向导与设置面板（OneDrive 风格，tkinter）。

- SetupWizard：首次启动——服务器地址 → 账号登录 → 同步文件夹 + 云端同步目录名
- open_settings：托盘「设置」——修改服务器/文件夹/账号/轮询间隔，保存后重启生效
"""
import json
import logging
import platform
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .api import Api, ApiError
from .paths import app_dir

logger = logging.getLogger("sync.gui")

CONFIG_PATH = app_dir() / "config.json"
FALLBACK_BRAND = "知识库"


def load_config() -> dict | None:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg.get("server_url") and cfg.get("sync_dir"):
                return cfg
        except Exception:
            logger.warning("config.json 解析失败", exc_info=True)
    return None


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_brand(server_url: str) -> str:
    """拉系统品牌名（公开接口）；失败给兜底名。"""
    try:
        return Api(server_url).brand().get("system_name") or FALLBACK_BRAND
    except Exception:
        return FALLBACK_BRAND


def default_cloud_folder() -> str:
    host = platform.node() or "本机"
    return f"同步-{host}"


class SyncSettingsForm:
    """设置表单（向导与设置面板共用）：服务器/账号/同步文件夹/云端目录/轮询间隔。

    parent 为 None → 独立 Tk（首次向导）；否则在 parent 上建 Toplevel（托盘设置）。"""

    def __init__(self, root, initial: dict | None, title: str, icon_png: Path | None):
        self.result: dict | None = None
        self._standalone = root is None
        self.root = root or tk.Tk()
        self.root.title(title)
        self.root.geometry("560x520")
        self.root.resizable(False, False)
        if icon_png and icon_png.exists():
            try:
                self.root.iconphoto(True, tk.PhotoImage(file=str(icon_png)))
            except Exception:
                pass
        self.initial = initial or {}
        self._dir_touched = "sync_dir" in self.initial
        self._build()

    def run(self) -> dict | None:
        if self._standalone:
            self.root.mainloop()
        else:
            self.root.transient(self.root.master)
            self.root.grab_set()
            self.root.wait_window()
        return self.result

    def _build(self):
        tk.Label(self.root, text="同步设置", font=("Microsoft YaHei UI", 15, "bold")).pack(pady=(22, 2))
        tk.Label(self.root, text="像 OneDrive 一样，把云端同步目录与本地文件夹保持一致", fg="#666").pack(pady=(0, 14))

        form = ttk.Frame(self.root)
        form.pack(fill="x", padx=28)

        def row(label, r):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=5)

        row("服务器地址", 0)
        self.server_var = tk.StringVar(value=self.initial.get("server_url", "http://10.1.30.37:8100"))
        ttk.Entry(form, textvariable=self.server_var, width=40).grid(row=0, column=1, pady=5)

        row("用户名", 1)
        self.user_var = tk.StringVar(value=self.initial.get("username", ""))
        ttk.Entry(form, textvariable=self.user_var, width=40).grid(row=1, column=1, pady=5)

        row("密码", 2)
        self.pwd_var = tk.StringVar(value=self.initial.get("password", ""))
        ttk.Entry(form, textvariable=self.pwd_var, show="●", width=40).grid(row=2, column=1, pady=5)

        row("本地同步文件夹", 3)
        dir_frame = ttk.Frame(form)
        dir_frame.grid(row=3, column=1, sticky="w", pady=5)
        default_dir = self.initial.get("sync_dir") or str(Path.home() / FALLBACK_BRAND)
        self.dir_var = tk.StringVar(value=default_dir)
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=30)
        dir_entry.pack(side="left")
        self.dir_var.trace_add("write", lambda *_: setattr(self, "_dir_touched", True))
        ttk.Button(dir_frame, text="浏览…", width=7, command=self._pick_dir).pack(side="left", padx=(6, 0))

        row("云端同步目录", 4)
        self.cloud_var = tk.StringVar(value=self.initial.get("folder_name") or default_cloud_folder())
        ttk.Entry(form, textvariable=self.cloud_var, width=40).grid(row=4, column=1, pady=5)
        tk.Label(self.root, text="云端文档库中会以此名字建一个文件夹，本地内容同步到其中",
                 fg="#888", font=("", 9)).pack()

        row("轮询间隔（秒）", 5)
        self.poll_var = tk.IntVar(value=int(self.initial.get("poll_interval", 30)))
        ttk.Spinbox(form, from_=5, to=3600, textvariable=self.poll_var, width=10).grid(row=5, column=1, sticky="w", pady=5)

        self.status_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status_var, fg="#d97706", wraplength=500).pack(pady=6)

        btns = ttk.Frame(self.root)
        btns.pack(pady=10)
        self.ok_btn = ttk.Button(btns, text="保存并开始同步", command=self._finish)
        self.ok_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=self.root.destroy).pack(side="left", padx=8)

    def _pick_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if chosen:
            self.dir_var.set(chosen)

    def _finish(self):
        server = self.server_var.get().strip().rstrip("/")
        username = self.user_var.get().strip()
        password = self.pwd_var.get()
        sync_dir = self.dir_var.get().strip()
        cloud_name = self.cloud_var.get().strip() or default_cloud_folder()
        if not all([server, username, password, sync_dir]):
            self.status_var.set("请完整填写服务器地址、用户名、密码和同步文件夹")
            return
        self.ok_btn.config(state="disabled")
        self.status_var.set("正在连接服务器并登录…")
        self.root.update_idletasks()
        try:
            api = Api(server)
            api.login(username, password)
            folder = api.ensure_folder(cloud_name)
        except ApiError as exc:
            self.status_var.set(f"连接失败：{exc}")
            self.ok_btn.config(state="normal")
            return
        except Exception as exc:
            self.status_var.set(f"无法连接服务器：{exc}")
            self.ok_btn.config(state="normal")
            return
        # 品牌名：用户没改过默认目录时，用系统名作为本地文件夹名
        brand = fetch_brand(server)
        if not self._dir_touched and brand != FALLBACK_BRAND:
            sync_dir = str(Path.home() / brand)
        Path(sync_dir).mkdir(parents=True, exist_ok=True)
        self.result = {
            "server_url": server,
            "username": username,
            "password": password,
            "sync_dir": sync_dir,
            "poll_interval": max(5, int(self.poll_var.get() or 30)),
            "folder_id": folder["id"],
            "folder_name": folder["name"],
        }
        self.root.destroy()


def ensure_config(icon_png: Path | None = None) -> dict | None:
    """有配置直接用；否则弹首次设置向导。用户取消返回 None。"""
    cfg = load_config()
    if cfg:
        return cfg
    return SyncSettingsForm(None, None, "同步 - 初始设置", icon_png).run()


def open_settings(current: dict, icon_png: Path | None = None,
                  brand_name: str = FALLBACK_BRAND, parent=None) -> dict | None:
    """托盘「设置」面板：预填当前配置，返回新配置（未保存返回 None）。"""
    top = tk.Toplevel(parent) if parent else None
    return SyncSettingsForm(top, current, f"{brand_name}同步 - 设置", icon_png).run()
