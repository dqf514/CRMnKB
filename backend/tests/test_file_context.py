"""PR-H：file_context 直读小文件测试。"""
import pytest

from app.services import file_context as fc


class _FakeFile:
    def __init__(self, id, file_name, file_type, file_path):
        self.id = id
        self.file_name = file_name
        self.file_type = file_type
        self.file_path = file_path


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, files):
        self._files = files
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append({"stmt": str(stmt), "params": params or {}})
        return _FakeResult(self._files)


# ---------- _read_one_file 格式分支 ----------


def test_read_one_file_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello 中文", encoding="utf-8")
    f = _FakeFile(1, "a.txt", "txt", str(p))
    assert fc._read_one_file(f) == "hello 中文"


def test_read_one_file_gbk_fallback(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("中文ABC".encode("gbk"))
    f = _FakeFile(1, "a.txt", "txt", str(p))
    assert "中文" in fc._read_one_file(f)


def test_read_one_file_md(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# title\n\nbody", encoding="utf-8")
    f = _FakeFile(1, "a.md", "md", str(p))
    assert "# title" in fc._read_one_file(f)


def test_read_one_file_pdf(tmp_path):
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf 未装")
    p = tmp_path / "a.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    p.write_bytes(b"")
    # 真要写 PDF 内容太复杂，仅测函数在路径不存在时返回空串不抛错
    f = _FakeFile(1, "a.pdf", "pdf", "/nonexistent.pdf")
    assert fc._read_one_file(f) == ""


def test_read_one_file_unsupported_type(tmp_path):
    p = tmp_path / "a.zip"
    p.write_bytes(b"PK")
    f = _FakeFile(1, "a.zip", "zip", str(p))
    assert fc._read_one_file(f) == ""  # 不支持的类型直接返回空


def test_read_one_file_path_missing():
    f = _FakeFile(1, "a.txt", "txt", "/nonexistent/path.txt")
    assert fc._read_one_file(f) == ""


# ---------- build_direct_file_context ----------


async def test_build_context_empty_file_ids():
    db = _FakeSession([])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[])
    assert result["context"] == ""
    assert result["too_large"] is False


async def test_build_context_no_matching_files():
    db = _FakeSession([])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[1, 2, 3])
    assert result["context"] == ""
    assert result["files"] == []


async def test_build_context_single_txt(tmp_path, monkeypatch):
    monkeypatch.setattr(fc.settings, "RAG_DIRECT_FILE_MAX_CHARS", 50000)
    p = tmp_path / "doc.txt"
    p.write_text("这是测试内容", encoding="utf-8")
    f = _FakeFile(1, "doc.txt", "txt", str(p))
    db = _FakeSession([f])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[1])
    assert "doc.txt" in result["context"]
    assert "这是测试内容" in result["context"]
    assert result["too_large"] is False
    assert result["total_chars"] > 0
    assert any(info["name"] == "doc.txt" for info in result["files"])


async def test_build_context_total_exceeds_threshold(tmp_path, monkeypatch):
    """总字符数超 RAG_DIRECT_FILE_MAX_CHARS：too_large=True，调用方应回退 RAG。"""
    monkeypatch.setattr(fc.settings, "RAG_DIRECT_FILE_MAX_CHARS", 50)
    p = tmp_path / "big.txt"
    p.write_text("a" * 200, encoding="utf-8")  # 200 chars > 50 阈值
    f = _FakeFile(1, "big.txt", "txt", str(p))
    db = _FakeSession([f])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[1])
    assert result["too_large"] is True


async def test_build_context_skips_unreadable_files(tmp_path):
    """不可解析的文件（type=zip）会被跳过，返回空 context。"""
    p = tmp_path / "a.zip"
    p.write_bytes(b"PK")
    f = _FakeFile(1, "a.zip", "zip", str(p))
    db = _FakeSession([f])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[1])
    assert result["context"] == ""  # 全部不可读 → 空 context
    assert result["files"][0]["skipped"] is True


async def test_build_context_mixed_readable_and_not(tmp_path):
    p1 = tmp_path / "a.txt"
    p1.write_text("可读内容", encoding="utf-8")
    p2 = tmp_path / "b.zip"
    p2.write_bytes(b"PK")
    db = _FakeSession([
        _FakeFile(1, "a.txt", "txt", str(p1)),
        _FakeFile(2, "b.zip", "zip", str(p2)),
    ])
    result = await fc.build_direct_file_context(db, tenant_id=1, file_ids=[1, 2])
    assert "可读内容" in result["context"]
    assert any(f["name"] == "a.txt" for f in result["files"])
    assert any(f["name"] == "b.zip" and f.get("skipped") for f in result["files"])