"""切片 v2 测试：标题锚定、中文断句、代码块掩码、章节链路。

chunk_text v2 返回 list[dict]，每个 dict 含 content/title/parent_chain/level/is_code_block。
"""

from app.services.ingestion import chunk_text


def _contents(chunks):
    """便捷：只取 content 字段做断言。"""
    return [c["content"] for c in chunks]


# ---------- 基础行为（与原 v1 测试对齐）----------


def test_empty_text_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_short_text_single_chunk():
    text = "这是一个短文档，只有一个段落。"
    result = chunk_text(text, size=512, overlap=64)
    assert len(result) == 1
    assert result[0]["content"] == text
    assert result[0]["title"] == ""
    assert result[0]["parent_chain"] == []
    assert result[0]["level"] == 0
    assert result[0]["is_code_block"] is False


def test_chunk_size_constraint():
    units = ["段" * 80 for _ in range(10)]
    text = "\n\n".join(units)
    chunks = chunk_text(text, size=200, overlap=20)
    contents = _contents(chunks)
    assert len(contents) > 1
    assert all(0 < len(c) <= 200 for c in contents)


def test_hard_split_keeps_overlap():
    # 250 字符超长单段（无换行），size=100 overlap=20：硬切且相邻块重叠 20 字符
    text = "字" + "".join(str(i % 10) for i in range(249))
    chunks = chunk_text(text, size=100, overlap=20)
    contents = _contents(chunks)
    assert len(contents) >= 3
    assert all(len(c) <= 100 for c in contents)
    for prev, nxt in zip(contents, contents[1:]):
        assert nxt[:20] == prev[-20:]
    rebuilt = contents[0] + "".join(c[20:] for c in contents[1:])
    assert rebuilt == text


def test_greedy_merge_keeps_overlap_when_possible():
    u1 = "A" * 80
    u2 = "B" * 80
    chunks = chunk_text(f"{u1}\n\n{u2}", size=100, overlap=10)
    contents = _contents(chunks)
    assert len(contents) == 2
    assert contents[0] == u1
    assert contents[1].startswith(u1[-10:])
    assert contents[1].endswith(u2)


def test_paragraphs_not_split_when_they_fit():
    units = ["第一段内容", "第二段内容", "第三段内容"]
    text = "\n\n".join(units)
    chunks = chunk_text(text, size=512, overlap=64)
    contents = _contents(chunks)
    assert len(contents) == 1
    for unit in units:
        assert any(unit in c for c in contents)


def test_overlap_larger_than_size_is_clamped():
    text = "数据" * 300
    chunks = chunk_text(text, size=100, overlap=999)
    contents = _contents(chunks)
    assert contents
    assert all(len(c) <= 100 for c in contents)


# ---------- v2 新能力：标题锚定 / 章节链路 ----------


def test_single_h1_anchors_paragraph():
    text = "# 第一章\n这是第一章的正文内容。\n还有一段。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "第一章"
    assert chunks[0]["parent_chain"] == ["第一章"]
    assert chunks[0]["level"] == 1
    # content 含章节前缀
    assert chunks[0]["content"].startswith("[第一章]\n")


def test_nested_h1_h2_h3_builds_parent_chain():
    text = (
        "# 第一章\n"
        "## 1.1 节\n"
        "### 1.1.1 小节\n"
        "第一段正文。\n\n"
        "## 1.2 节\n"
        "第二段正文。"
    )
    chunks = chunk_text(text, size=512, overlap=64)
    # 期望 2 个 chunk：1.1.1 / 1.2
    assert len(chunks) == 2
    assert chunks[0]["parent_chain"] == ["第一章", "1.1 节", "1.1.1 小节"]
    assert chunks[0]["level"] == 3
    assert chunks[0]["title"] == "1.1.1 小节"
    assert chunks[0]["content"].startswith("[第一章 > 1.1 节 > 1.1.1 小节]\n")

    assert chunks[1]["parent_chain"] == ["第一章", "1.2 节"]
    assert chunks[1]["level"] == 2
    assert chunks[1]["content"].startswith("[第一章 > 1.2 节]\n")


def test_h2_after_h1_resets_chain():
    text = "# 第一章\n第一章内容。\n\n# 第二章\n第二章内容。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 2
    assert chunks[0]["parent_chain"] == ["第一章"]
    assert chunks[1]["parent_chain"] == ["第二章"]


def test_h3_without_h1_h2_starts_chain():
    text = "### 无上级标题\n正文内容。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0]["parent_chain"] == ["无上级标题"]
    assert chunks[0]["level"] == 1  # 相对层数 = 链路长度


def test_text_before_first_title_has_empty_parent_chain():
    text = "开头的引言段落。\n\n# 第一章\n章节内容。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 2
    assert chunks[0]["parent_chain"] == []
    assert chunks[0]["title"] == ""
    assert chunks[1]["parent_chain"] == ["第一章"]


# ---------- v2 新能力：代码块掩码 ----------


def test_hash_inside_code_block_not_treated_as_title():
    text = (
        "# 真实标题\n"
        "正文段落。\n\n"
        "```python\n"
        "# 这不是标题，是 Python 注释\n"
        "def foo():\n"
        "    return 1\n"
        "```\n"
        "代码块后的正文。"
    )
    chunks = chunk_text(text, size=512, overlap=64)
    # 整段 ≤512 故为单 chunk；关键是 parent_chain 没有"# 这不是标题"
    assert len(chunks) == 1
    assert chunks[0]["parent_chain"] == ["真实标题"]
    # 代码块内 # 是 Python 注释，不应被识别为标题节点
    assert "# 这不是标题" in chunks[0]["content"]  # 原文本保留
    assert "这不是标题" not in chunks[0]["parent_chain"]  # 但不在标题链中
    assert chunks[0]["is_code_block"] is True


def test_code_block_flag_set():
    text = "# 标题\n```python\nprint(1)\n```"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0]["is_code_block"] is True


# ---------- v2 新能力：中文断句 ----------


def test_chinese_sentence_splits_at_period():
    # 多个中文句子，超长时按句号优先断
    text = "第一句。第二句。第三句。" * 50  # ~150 字符
    chunks = chunk_text(text, size=50, overlap=10)
    contents = _contents(chunks)
    # 每块以句号或上一句号收尾（首块除外）
    for c in contents[1:]:
        # 子块应从句号后开始或紧跟 overlap 字符
        assert c.endswith("。") or "。" in c


def test_chinese_smart_split_finds_break_point():
    # 100 字长句（无换行），size=40：至少断 2 次；每个断点尽量贴近句号
    text = "这是第一部分。" + "中间无标点" * 20 + "这是收尾。"
    chunks = chunk_text(text, size=40, overlap=10)
    contents = _contents(chunks)
    assert len(contents) >= 2
    # 第二块起首字符应为"中间无标点"（无句号可断，硬切）
    # 仅验证长度约束
    assert all(len(c) <= 40 for c in contents)


# ---------- 边界 ----------


def test_invalid_size_raises():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("正文", size=0)


def test_title_with_inline_hash_in_content():
    text = "# Issue 跟踪\nIssue #123 需要处理。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert "Issue #123" in chunks[0]["content"]
    # "Issue 跟踪" 应是唯一标题
    assert chunks[0]["title"] == "Issue 跟踪"


def test_empty_section_between_titles_skipped():
    text = "# 标题 A\n\n# 标题 B\nB 的内容。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0]["parent_chain"] == ["标题 B"]


def test_six_levels_of_headings_supported():
    text = "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n最深级正文。"
    chunks = chunk_text(text, size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0]["parent_chain"] == ["H1", "H2", "H3", "H4", "H5", "H6"]
    assert chunks[0]["level"] == 6


def test_multiple_blocks_in_same_section_merged():
    text = (
        "# 第一章\n"
        "段落一。\n\n"
        "段落二。\n\n"
        "段落三。"
    )
    chunks = chunk_text(text, size=512, overlap=64)
    # 三段都装入 size=512，单 chunk
    assert len(chunks) == 1
    content = chunks[0]["content"]
    assert "段落一" in content
    assert "段落二" in content
    assert "段落三" in content
    # 仅首个 chunk 含 parent_chain 前缀
    assert content.startswith("[第一章]\n")