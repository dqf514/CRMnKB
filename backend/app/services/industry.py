from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.industry import Industry
from app.models.tenant import Tenant

# 预置全行业清单（sort 按列表顺序）
PRESET_INDUSTRIES = [
    "互联网/IT",
    "软件与信息服务",
    "金融",
    "保险",
    "房地产与建筑",
    "制造业",
    "批发与零售",
    "教育培训",
    "医疗健康",
    "交通运输与物流",
    "住宿与餐饮",
    "文化与传媒",
    "能源与公用事业",
    "农林牧渔",
    "政府与公共事业",
    "专业服务（咨询/法律/会计）",
    "通信",
    "汽车",
    "消费电子",
    "服装纺织",
    "化工与新材料",
    "其他",
]


async def seed_industries(session: AsyncSession) -> int:
    """为没有任何行业记录的租户插入预置行业清单（幂等）。返回插入条数。"""
    tenant_ids = (await session.execute(select(Tenant.id))).scalars().all()
    inserted = 0
    for tenant_id in tenant_ids:
        count = await session.scalar(
            select(func.count()).select_from(Industry).where(Industry.tenant_id == tenant_id)
        )
        if count:
            continue
        for i, name in enumerate(PRESET_INDUSTRIES):
            session.add(Industry(tenant_id=tenant_id, name=name, sort=i))
            inserted += 1
    return inserted


async def migrate_legacy_industry(session: AsyncSession) -> int:
    """一次性数据迁移：旧 industry 单列并入 industries 数组。
    幂等：仅处理 industries 为空数组且 industry 非空的行。返回迁移行数。"""
    rows = (
        (
            await session.execute(
                select(Customer).where(
                    Customer.industry.isnot(None),
                    or_(Customer.industries.is_(None), Customer.industries == []),
                )
            )
        )
        .scalars()
        .all()
    )
    migrated = 0
    for customer in rows:
        if not customer.industry.strip():
            continue
        customer.industries = [customer.industry]
        migrated += 1
    return migrated
