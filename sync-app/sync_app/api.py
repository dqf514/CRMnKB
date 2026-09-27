"""后端 API 封装：登录（长效 token）/上传/更新内容/变更/下载/删除。"""
import logging
from pathlib import Path

import httpx

logger = logging.getLogger("sync.api")


class ApiError(Exception):
    pass


class _ProgressReader:
    """文件读取包装：httpx 分块读取时回调上传进度。"""

    def __init__(self, f, total: int, on_progress):
        self._f = f
        self._total = total
        self._on_progress = on_progress
        self._read = 0

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        if data:
            self._read += len(data)
            if self._on_progress:
                self._on_progress(self._read, self._total)
        return data

    def __getattr__(self, name):  # fileno/seek/tell 等代理给真实文件对象
        return getattr(self._f, name)


class Api:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=httpx.Timeout(60.0, read=300.0))
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    def set_token(self, token: str) -> None:
        self.client.headers["Authorization"] = f"Bearer {token}"

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise ApiError("AUTH_EXPIRED")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            raise ApiError(f"HTTP {resp.status_code}: {detail}")
        return resp

    def login(self, username: str, password: str) -> str:
        resp = self.client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password, "long_lived": True},
        )
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text[:200])
            raise ApiError(f"登录失败: {detail}")
        token = resp.json()["access_token"]
        self.set_token(token)
        return token

    def upload_new(self, local_path: Path, rel_path: str, folder_id: int | None = None,
                   on_progress=None) -> dict:
        """新建上传（保留目录结构）。返回 {files: [{id, file_name}], ...}"""
        data = {"paths": rel_path}
        if folder_id is not None:
            data["folder_id"] = str(folder_id)
        total = local_path.stat().st_size
        with open(local_path, "rb") as f:
            reader = _ProgressReader(f, total, on_progress)
            resp = self.client.post(
                f"{self.base_url}/api/v1/library/upload",
                files={"files": (local_path.name, reader)},
                data=data,
            )
        return self._check(resp).json()

    def update_content(self, file_id: int, local_path: Path, on_progress=None) -> dict:
        """更新已有文件内容 → 服务端自动触发关联 KB 重解析。"""
        total = local_path.stat().st_size
        with open(local_path, "rb") as f:
            reader = _ProgressReader(f, total, on_progress)
            resp = self.client.put(
                f"{self.base_url}/api/v1/library/files/{file_id}/content",
                files={"file": (local_path.name, reader)},
            )
        return self._check(resp).json()

    def get_file(self, file_id: int) -> dict:
        resp = self.client.get(f"{self.base_url}/api/v1/library/files/{file_id}")
        return self._check(resp).json()

    def changes(self, since_iso: str, folder_id: int | None = None, limit: int = 2000) -> dict:
        params: dict = {"since": since_iso, "limit": limit}
        if folder_id is not None:
            params["folder_id"] = folder_id
        resp = self.client.get(f"{self.base_url}/api/v1/library/changes", params=params)
        return self._check(resp).json()

    def download(self, file_id: int, dest: Path, total: int = 0, on_progress=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".sync-part")
        transferred = 0
        with self.client.stream(
            "GET", f"{self.base_url}/api/v1/library/files/{file_id}/content"
        ) as resp:
            self._check(resp)
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
                    transferred += len(chunk)
                    if on_progress:
                        on_progress(transferred, total)
        tmp.replace(dest)

    def delete_file(self, file_id: int) -> None:
        resp = self.client.delete(f"{self.base_url}/api/v1/library/files/{file_id}")
        self._check(resp)

    # ---- 品牌 / SSO / 文件夹 ----
    def brand(self) -> dict:
        """公开品牌信息（系统名等），无需登录。"""
        resp = self.client.get(f"{self.base_url}/api/v1/brand")
        resp.raise_for_status()
        return resp.json()

    def sso_code(self) -> str:
        """换一次性 SSO 码（打开网页版免登用）。"""
        resp = self.client.post(f"{self.base_url}/api/v1/auth/sso-code")
        return self._check(resp).json()["code"]

    def folder_tree(self) -> list:
        resp = self.client.get(f"{self.base_url}/api/v1/library/tree")
        return self._check(resp).json()

    def create_folder(self, name: str) -> dict:
        resp = self.client.post(f"{self.base_url}/api/v1/library/folders", json={"name": name})
        return self._check(resp).json()

    def ensure_folder(self, name: str) -> dict:
        """按名字找根目录文件夹，没有则创建。返回 {id, name}。"""
        for node in self.folder_tree():
            if node.get("name") == name:
                return {"id": node["id"], "name": node["name"]}
        return self.create_folder(name)
