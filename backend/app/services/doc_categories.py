"""客户文档资料类型（category）配置：存 system_settings，管理端可增删改。

- 取数：`GET /library/categories`（登录用户）/ `GET /admin/settings/doc-categories`（admin）。
- 维护：`PUT /admin/settings/doc-categories` 整体替换（admin）。
- 删除某个类型不影响存量文件的 category 值，展示端对未知值回退显示原始字符串。
"""
import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

DOC_CATEGORIES_KEY = "doc_categories"

DEFAULT_DOC_CATEGORIES = [
    {"value": "company_intro", "label": "公司介绍"},
    {"value": "monthly_report", "label": "月报"},
    {"value": "factsheet", "label": "产品资料"},
    {"value": "meeting_minutes", "label": "会议纪要"},
    {"value": "other", "label": "其他"},
]

_VALUE_PATTERN = re.compile(r"^[a-z0-9_]+$")
MAX_CATEGORIES = 20


def parse_doc_categories(value: str | None) -> list[dict]:
    """解析配置（纯函数）：空/脏数据回退默认；过滤缺字段与重复 value。"""
    if not value:
        return [dict(c) for c in DEFAULT_DOC_CATEGORIES]
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [dict(c) for c in DEFAULT_DOC_CATEGORIES]
    items: list[dict] = []
    seen: set[str] = set()
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        v = str(it.get("value") or "").strip()
        label = str(it.get("label") or "").strip()
        if v and label and v not in seen:
            seen.add(v)
            items.append({"value": v, "label": label})
    return items or [dict(c) for c in DEFAULT_DOC_CATEGORIES]


async def get_doc_categories(db: AsyncSession) -> list[dict]:
    row = await db.get(SystemSetting, DOC_CATEGORIES_KEY)
    return parse_doc_categories(row.value if row else None)


async def category_values(db: AsyncSession) -> set[str]:
    return {c["value"] for c in await get_doc_categories(db)}


def validate_categories_payload(items: list) -> list[dict]:
    """管理端整体替换校验（纯函数）。非法抛 ValueError，正常返回清洗后的列表。"""
    if not isinstance(items, list) or not items:
        raise ValueError("至少保留一个资料类型")
    if len(items) > MAX_CATEGORIES:
        raise ValueError(f"资料类型最多 {MAX_CATEGORIES} 个")
    cleaned: list[dict] = []
    seen: set[str] = set()
    for it in items:
        v = str(it.get("value") or "").strip() if isinstance(it, dict) else ""
        label = str(it.get("label") or "").strip() if isinstance(it, dict) else ""
        if not _VALUE_PATTERN.match(v):
            raise ValueError(f"类型标识「{v}」仅支持小写字母/数字/下划线")
        if not label:
            raise ValueError(f"类型「{v}」缺少显示名称")
        if v in seen:
            raise ValueError(f"类型标识「{v}」重复")
        seen.add(v)
        cleaned.append({"value": v, "label": label})
    return cleaned
