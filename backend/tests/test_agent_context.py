"""dsh agent 模式关联范围上下文注入测试（agent_context.build_attached_context）。

用 aiosqlite 内存库真实 SQL 验证：
- 关联知识库生成 kb_id 列表块
- 关联文件直读全文（真实磁盘文件）；直读不可用时回退列 doc_id 引导 kb_read_doc
- 越权 id 被 ACL 过滤（他人私有资源不注入）
- 无有效关联返回空串

建表子集与 JSONB→JSON swap 手法同 test_permissions_acl_real.py。
"""
import pytest
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.document import KnowledgeDocument
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.resource_permission import ResourcePermission
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_group import UserGroup
from app.services.agent_context import build_attached_context

_TABLES = [
    Tenant.__table__,
    UserGroup.__table__,
    User.__table__,
    LibraryFolder.__table__,
    LibraryFile.__table__,
    ResourcePermission.__table__,
    KnowledgeBase.__table__,
    KnowledgeDocument.__table__,
]


@pytest.fixture
async def db():
    swapped_types: list[tuple] = []
    saved_indexes: dict = {}
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped_types.append((col, col.type))
                col.type = JSON()
        saved_indexes[table] = set(table.indexes)
        table.indexes.clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add_all([
                Tenant(id=1, name="租户一"),
                UserGroup(id=1, tenant_id=1, name="团队一"),
                User(id=1, tenant_id=1, username="admin", password_hash="x", name="管理员", role="admin", group_id=1),
                User(id=2, tenant_id=1, username="alice", password_hash="x", name="甲", role="member", group_id=1),
                User(id=3, tenant_id=1, username="bob", password_hash="x", name="乙", role="member", group_id=1),
            ])
            await session.commit()
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


def _kb(kid, owner_id, *, private=True, name=None):
    return KnowledgeBase(
        id=kid, tenant_id=1, name=name or f"kb{kid}", type="general",
        is_auto=False, owner_id=owner_id, is_private=private,
    )


def _file(fid, owner_id, *, private=True, file_path=None, name=None):
    return LibraryFile(
        id=fid, tenant_id=1, folder_id=None,
        file_name=name or f"f{fid}.txt", file_path=file_path or f"/nonexist/f{fid}.txt",
        file_type="txt", file_size=10, supported=True, owner_id=owner_id, is_private=private,
    )


async def test_no_attachments_returns_empty(db):
    alice = await db.get(User, 2)
    assert await build_attached_context(db, alice) == ""
    assert await build_attached_context(db, alice, [], []) == ""


async def test_kb_ids_generate_kb_block(db):
    db.add(_kb(1, owner_id=2, name="项目资料库"))
    db.add(_kb(2, owner_id=2, private=False, name="团队共享库"))
    await db.commit()
    alice = await db.get(User, 2)
    block = await build_attached_context(db, alice, kb_ids=[1, 2])
    assert "<attached-context>" in block
    assert "kb_id=1《项目资料库》" in block
    assert "kb_id=2《团队共享库》" in block


async def test_kb_ids_acl_filters_unauthorized(db):
    db.add(_kb(1, owner_id=2, name="alice私有库"))
    db.add(_kb(2, owner_id=3, name="bob私有库"))
    await db.commit()
    alice = await db.get(User, 2)
    # bob 的私有库被过滤，只列 alice 自己的
    block = await build_attached_context(db, alice, kb_ids=[1, 2])
    assert "kb_id=1《alice私有库》" in block
    assert "bob私有库" not in block
    # 全是越权 id 时返回空串
    assert await build_attached_context(db, alice, kb_ids=[2]) == ""


async def test_file_direct_read_injected(db, tmp_path):
    p = tmp_path / "meeting.txt"
    p.write_text("会议纪要：下周三与 Alex 复盘。", encoding="utf-8")
    db.add(_file(1, owner_id=2, file_path=str(p), name="meeting.txt"))
    await db.commit()
    alice = await db.get(User, 2)
    block = await build_attached_context(db, alice, file_ids=[1])
    assert "【关联文档全文】" in block
    assert "会议纪要：下周三与 Alex 复盘。" in block
    # 直读成功就不应再列 doc_id 引导块
    assert "kb_read_doc 按 doc_id" not in block


async def test_file_fallback_lists_doc_id(db):
    # 磁盘文件不存在 → 直读为空 → 回退列解析完成文档的 doc_id
    db.add(_file(1, owner_id=2, file_path="/nonexist/x.txt", name="x.txt"))
    db.add(KnowledgeDocument(
        id=10, tenant_id=1, kb_id=None, file_id=1,
        title="已解析文档", file_name="x.txt", file_path="/nonexist/x.txt",
        file_type="txt", status="ready",
    ))
    db.add(KnowledgeDocument(
        id=11, tenant_id=1, kb_id=None, file_id=1,
        title="未解析完文档", file_name="y.txt", file_path="/nonexist/y.txt",
        file_type="txt", status="processing",
    ))
    await db.commit()
    alice = await db.get(User, 2)
    block = await build_attached_context(db, alice, file_ids=[1])
    assert "doc_id=10《已解析文档》" in block
    # processing 状态的不列
    assert "未解析完文档" not in block


async def test_file_ids_acl_filters_unauthorized(db, tmp_path):
    p = tmp_path / "secret.txt"
    p.write_text("bob 的私密内容", encoding="utf-8")
    db.add(_file(1, owner_id=3, file_path=str(p), name="secret.txt"))
    await db.commit()
    alice = await db.get(User, 2)
    # bob 私有文件对 alice 不可见 → 不注入任何内容
    assert await build_attached_context(db, alice, file_ids=[1]) == ""


async def test_mixed_kb_and_file(db, tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("随手记内容", encoding="utf-8")
    db.add(_kb(1, owner_id=2, name="混合库"))
    db.add(_file(1, owner_id=2, file_path=str(p), name="note.txt"))
    await db.commit()
    alice = await db.get(User, 2)
    block = await build_attached_context(db, alice, kb_ids=[1], file_ids=[1])
    assert "kb_id=1《混合库》" in block
    assert "随手记内容" in block
