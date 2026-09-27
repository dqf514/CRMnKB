import pytest

from app.services import chat as chat_module
from app.services.ingestion import is_supported
from app.services.kb import (
    build_tree,
    parse_upload_path,
    plan_associations,
    should_migrate,
)


# ---------- is_supported ----------

def test_is_supported():
    assert is_supported("合同.pdf")
    assert is_supported("纪要.DOCX")  # 大小写不敏感
    assert is_supported("notes.txt")
    assert is_supported("readme.md")
    assert not is_supported("压缩包.zip")
    assert is_supported("表格.xlsx")  # 现已支持 Excel
    assert not is_supported("无扩展名")
    assert not is_supported("")


# ---------- 上传相对路径解析 ----------

def test_parse_upload_path_nested():
    assert parse_upload_path("a/b/c.txt") == (["a", "b"], "c.txt")


def test_parse_upload_path_root_file():
    assert parse_upload_path("c.txt") == ([], "c.txt")


def test_parse_upload_path_strips_dots_and_backslashes():
    assert parse_upload_path("./a\\b\\c.txt") == (["a", "b"], "c.txt")
    assert parse_upload_path("a/./b/../c.txt") == (["a", "b"], "c.txt")
    assert parse_upload_path("") == ([], "")


# ---------- 文件夹树组装 ----------

def test_build_tree_nesting():
    folders = [
        {"id": 1, "name": "根目录A", "parent_id": None},
        {"id": 2, "name": "子目录", "parent_id": 1},
        {"id": 3, "name": "孙目录", "parent_id": 2},
        {"id": 4, "name": "根目录B", "parent_id": None},
    ]
    tree = build_tree(folders)
    assert [n["name"] for n in tree] == ["根目录A", "根目录B"]
    assert tree[0]["children"][0]["name"] == "子目录"
    assert tree[0]["children"][0]["children"][0]["name"] == "孙目录"
    assert tree[1]["children"] == []


def test_build_tree_orphan_goes_root():
    folders = [{"id": 9, "name": "孤儿", "parent_id": 999}]
    tree = build_tree(folders)
    assert [n["id"] for n in tree] == [9]


# ---------- 关联去重 ----------

def test_plan_associations_dedup():
    todo, already = plan_associations([1, 2, 2, 3, 4], existing_file_ids={2, 3})
    assert todo == [1, 4]
    assert already == 3  # 2(已存在) + 2(重复) + 3(已存在)


def test_plan_associations_empty():
    assert plan_associations([], set()) == ([], 0)


# ---------- 幂等迁移条件 ----------

def test_should_migrate():
    assert should_migrate(doc_count=5, kb_count=0) is True
    assert should_migrate(doc_count=0, kb_count=0) is False
    assert should_migrate(doc_count=5, kb_count=1) is False  # 已有知识库 → 不重复迁移


# ---------- kb_ids 过滤检索（链路 plumbing） ----------

class _FakeEmbed:
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3]]


async def test_prepare_passes_kb_ids_to_search(monkeypatch):
    captured = {}

    async def fake_vector(db, tid, vec, limit, kb_ids=None, file_ids=None):
        captured["vector_kb_ids"] = kb_ids
        captured["file_ids"] = file_ids
        return []

    async def fake_keyword(db, tid, q, limit, kb_ids=None, file_ids=None):
        captured["keyword_kb_ids"] = kb_ids
        return []

    monkeypatch.setattr(chat_module, "search_chunks_vector", fake_vector)
    monkeypatch.setattr(chat_module, "search_chunks_keyword", fake_keyword)

    async def _resolve_embed(caller=None, **kw):
        return _FakeEmbed()

    monkeypatch.setattr(chat_module, "resolve_embed_llm", _resolve_embed)

    result = await chat_module._prepare(None, 1, "问题", [], kb_ids=[1, 2])
    assert captured["vector_kb_ids"] == [1, 2]
    assert captured["keyword_kb_ids"] == [1, 2]
    assert result["grounded"] is False


async def test_prepare_default_searches_all(monkeypatch):
    captured = {}

    async def fake_vector(db, tid, vec, limit, kb_ids=None, file_ids=None):
        captured["kb_ids"] = kb_ids
        captured["file_ids"] = file_ids
        return []

    async def fake_keyword(db, tid, q, limit, kb_ids=None, file_ids=None):
        return []

    monkeypatch.setattr(chat_module, "search_chunks_vector", fake_vector)
    monkeypatch.setattr(chat_module, "search_chunks_keyword", fake_keyword)

    async def _resolve_embed(caller=None, **kw):
        return _FakeEmbed()

    monkeypatch.setattr(chat_module, "resolve_embed_llm", _resolve_embed)

    await chat_module._prepare(None, 1, "问题", [])
    assert captured["kb_ids"] is None  # 缺省检索全部知识库
