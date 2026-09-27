from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import or_, select
from sqlalchemy.dialects import postgresql

from app.api import deps
from app.database import get_db
from app.main import app
from app.models.customer import Customer
from app.services.industry import (
    PRESET_INDUSTRIES,
    migrate_legacy_industry,
    seed_industries,
)


# ---------------------------------------------------------------------------
# 假会话 / 客户端基础设施（与 test_admin 同款队列式）
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._scalar_queue = []
        self._get_queue = []
        self.added = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_scalar(self, value):
        self._scalar_queue.append(value)

    def queue_get(self, value):
        self._get_queue.append(value)

    async def execute(self, stmt):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 8, 14)
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = datetime(2026, 8, 14)

    async def delete(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role="user")

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _industry_ns(id=1, name="金融", sort=0, enabled=True, tenant_id=1):
    return SimpleNamespace(
        id=id, tenant_id=tenant_id, name=name, sort=sort, enabled=enabled,
        created_at=datetime(2026, 8, 14),
    )


# ---------------------------------------------------------------------------
# 行业 CRUD
# ---------------------------------------------------------------------------

async def test_list_industries(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute([_industry_ns(1, "互联网/IT", 0), _industry_ns(2, "金融", 1)])
    resp = await client.get("/api/v1/industries")
    assert resp.status_code == 200
    data = resp.json()
    assert [i["name"] for i in data] == ["互联网/IT", "金融"]
    assert data[0]["sort"] == 0 and data[0]["enabled"] is True


async def test_create_industry_201(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(0)  # 同名不存在
    resp = await client.post("/api/v1/industries", json={"name": "新能源", "sort": 22})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "新能源"
    assert data["sort"] == 22
    assert data["enabled"] is True


async def test_create_industry_conflict_409(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(1)  # 同租户同名已存在
    resp = await client.post("/api/v1/industries", json={"name": "金融"})
    assert resp.status_code == 409


async def test_update_industry(client):
    db = _FakeSession()
    _override(db)
    industry = _industry_ns()
    db.queue_get(industry)
    db.queue_scalar(0)  # 改名不冲突
    resp = await client.put("/api/v1/industries/1", json={"name": "大金融", "enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "大金融"
    assert data["enabled"] is False


async def test_update_industry_name_conflict_409(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_industry_ns())
    db.queue_scalar(1)
    resp = await client.put("/api/v1/industries/1", json={"name": "保险"})
    assert resp.status_code == 409


async def test_delete_industry_204(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_industry_ns())
    resp = await client.delete("/api/v1/industries/1")
    assert resp.status_code == 204


async def test_industry_cross_tenant_404(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_industry_ns(tenant_id=2))
    resp = await client.delete("/api/v1/industries/1")
    assert resp.status_code == 404


async def test_industries_require_auth(client):
    assert (await client.get("/api/v1/industries")).status_code == 401


# ---------------------------------------------------------------------------
# 预置种子（幂等）与旧 industry 迁移
# ---------------------------------------------------------------------------

async def test_seed_industries_inserts_presets():
    db = _FakeSession()
    db.queue_execute([1])  # 租户 id 列表
    db.queue_scalar(0)  # 该租户无行业记录
    inserted = await seed_industries(db)
    assert inserted == len(PRESET_INDUSTRIES) == 22
    names = [i.name for i in db.added]
    assert names == PRESET_INDUSTRIES
    assert [i.sort for i in db.added] == list(range(22))  # sort 按顺序


async def test_seed_industries_idempotent():
    db = _FakeSession()
    db.queue_execute([1])
    db.queue_scalar(22)  # 已有记录 → 不重复插入
    assert await seed_industries(db) == 0
    assert db.added == []


async def test_migrate_legacy_industry_merges():
    db = _FakeSession()
    c1 = SimpleNamespace(industry="软件", industries=[])
    c2 = SimpleNamespace(industry="  ", industries=[])  # 空白跳过
    db.queue_execute([c1, c2])
    migrated = await migrate_legacy_industry(db)
    assert migrated == 1
    assert c1.industries == ["软件"]
    assert c2.industries == []


async def test_migrate_legacy_industry_idempotent():
    db = _FakeSession()
    db.queue_execute([])  # 二次执行：查询无命中行
    assert await migrate_legacy_industry(db) == 0


# ---------------------------------------------------------------------------
# 客户新字段 + 筛选
# ---------------------------------------------------------------------------

def _customer_ns(**kw):
    base = dict(
        id=1, tenant_id=1, owner_id=1, name="张三",
        industries=["金融"], tags=["VIP"], company="某某科技", position="总监",
        wechat="wx123", phone="138", email=None, address=None, birthday=None,
        attributes={}, status="intention", source=None, deleted_at=None,
        created_at=datetime(2026, 8, 14), updated_at=datetime(2026, 8, 14),
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_create_customer_with_new_fields(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": "张三",
            "industries": ["金融", "保险"],
            "tags": ["VIP"],
            "company": "某某科技",
            "position": "总监",
            "wechat": "wx123",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["industries"] == ["金融", "保险"]
    assert data["tags"] == ["VIP"]
    assert data["company"] == "某某科技"
    assert data["position"] == "总监"
    assert data["wechat"] == "wx123"
    assert "industry" not in data  # 旧单列已从契约移除


async def test_create_customer_defaults_empty_arrays(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post("/api/v1/customers", json={"name": "李四"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["industries"] == [] and data["tags"] == []
    assert data["company"] is None


async def test_update_customer_new_fields(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_customer_ns())
    resp = await client.put(
        "/api/v1/customers/1", json={"tags": ["重点"], "company": "新公司"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tags"] == ["重点"]
    assert data["company"] == "新公司"
    assert data["industries"] == ["金融"]  # 未传字段不变


async def test_list_customers_with_industry_tag_keyword_filters(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(1)  # total
    db.queue_execute([_customer_ns()])
    resp = await client.get(
        "/api/v1/customers", params={"industry": "金融", "tag": "VIP", "keyword": "科技"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["company"] == "某某科技"


def test_filter_sql_compiles_for_postgres():
    """industry/tag 用 JSONB ? 操作符、keyword 覆盖 company 列，SQL 可正确编译。"""
    like = "%科技%"
    stmt = select(Customer).where(
        or_(
            Customer.name.like(like),
            Customer.phone.like(like),
            Customer.email.like(like),
            Customer.company.like(like),
        ),
        Customer.industries.has_key("金融"),  # noqa: W601
        Customer.tags.has_key("VIP"),  # noqa: W601
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "customers.industries ?" in sql
    assert "customers.tags ?" in sql
    assert "customers.company LIKE" in sql
