"""批次四安全/审计缺口修复的回归测试：skill config 递归脱敏、自动建目录 owner 口径。"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin_llm import mask_api_key
from app.api.admin_skills import _mask_config
from app.models.library_folder import LibraryFolder
from app.services.kb import get_or_create_folder


# ---------------------------------------------------------------------------
# skill config 递归脱敏（嵌套 headers.Authorization 等也必须脱敏）
# ---------------------------------------------------------------------------

def test_mask_config_recursive_nested_dict_and_list():
    cfg = {
        "url": "https://api.example.com",
        "headers": {
            "Authorization": "Bearer abcdefgh1234",
            "X-Custom": "plain-value",
        },
        "steps": [
            {"name": "s1", "api_key": "key123456789"},
            {"name": "s2", "note": "不含敏感键"},
        ],
    }
    masked = _mask_config(cfg)
    # 嵌套敏感键被脱敏
    assert masked["headers"]["Authorization"] == mask_api_key("Bearer abcdefgh1234")
    assert masked["steps"][0]["api_key"] == mask_api_key("key123456789")
    # 非敏感键原样保留
    assert masked["url"] == "https://api.example.com"
    assert masked["headers"]["X-Custom"] == "plain-value"
    assert masked["steps"][1]["note"] == "不含敏感键"
    # 不原地修改入参
    assert cfg["headers"]["Authorization"] == "Bearer abcdefgh1234"


def test_mask_config_top_level_still_masked_and_none_safe():
    masked = _mask_config({"app_secret": "s3cr3t-value", "timeout": 30})
    assert masked["app_secret"] == mask_api_key("s3cr3t-value")
    assert masked["timeout"] == 30
    assert _mask_config(None) == {}
    assert _mask_config({}) == {}


# ---------------------------------------------------------------------------
# get_or_create_folder：owner 口径（防文件落进他人私有目录 / 孤儿私有目录）
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._get_map = {}
        self.added = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_get(self, model, ident, value):
        self._get_map[(model, ident)] = value

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return None

    async def get(self, model, ident):
        return self._get_map.get((model, ident))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 100


def _user(uid=7, role="user"):
    return SimpleNamespace(id=uid, tenant_id=1, username=f"u{uid}", role=role, status=1)


async def test_auto_create_root_folder_sets_owner_and_private():
    """根目录自动建目录：owner 落创建者，默认私有（不再是 owner=NULL 的孤儿目录）。"""
    db = _FakeSession()
    db.queue_execute(None)  # 无同名文件夹
    folder = await get_or_create_folder(db, 1, None, "资料", _user())
    assert folder.owner_id == 7
    assert folder.is_private is True


async def test_auto_create_subfolder_inherits_parent_privacy():
    """父目录团队可见时，自动建的子目录继承其可见性，owner 落创建者。"""
    parent = LibraryFolder(id=9, tenant_id=1, parent_id=None, name="公共", owner_id=9, is_private=False)
    db = _FakeSession()
    db.queue_execute(None)  # 无同名子目录
    db.queue_get(LibraryFolder, 9, parent)
    folder = await get_or_create_folder(db, 1, 9, "子目录", _user())
    assert folder.owner_id == 7
    assert folder.is_private is False


async def test_existing_private_folder_of_other_user_403():
    """命中他人私有文件夹：无 edit 权限 → 403，文件不会落进去。"""
    other_folder = LibraryFolder(id=5, tenant_id=1, parent_id=None, name="私有", owner_id=9, is_private=True)
    db = _FakeSession()
    db.queue_execute(other_folder)  # 命中已存在文件夹
    db.queue_get(LibraryFolder, 5, other_folder)  # ensure_access → get_access 读自身
    db.queue_execute([SimpleNamespace(id=5, parent_id=None)])  # _folder_graph
    with pytest.raises(HTTPException) as exc:
        await get_or_create_folder(db, 1, None, "私有", _user())
    assert exc.value.status_code == 403


async def test_existing_folder_without_user_keeps_legacy_behavior():
    """不传 user（内部兼容路径）：命中即返回，不做权限校验。"""
    folder = LibraryFolder(id=5, tenant_id=1, parent_id=None, name="x", owner_id=9, is_private=True)
    db = _FakeSession()
    db.queue_execute(folder)
    got = await get_or_create_folder(db, 1, None, "x")
    assert got is folder
