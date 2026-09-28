"""系统设置（管理端）：品牌设置 + 解析文件格式开关 + 登录与接入。

- 品牌配置沿用 /api/v1/brand（公开读 + 管理员改），前端并入本页"品牌设置"Tab。
- 解析格式开关存 system_settings（key=parse_formats_enabled），本接口读写并刷新
  进程内缓存；关闭的格式上传/关联/重试时一律不解析。
- 登录与接入存 system_settings（key=login_integrations）：短信验证码通道
  （log/http 网关）与微信登录预留配置（app_secret 加密存储、脱敏返回）。
- 文档资料类型存 system_settings（key=doc_categories）：客户文档 category 的
  可选值列表，管理端整体替换维护，上传/改标签时按此校验。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.schemas.library import DocCategoriesUpdate
from app.services.audit import record_audit
from app.services.doc_categories import (
    DOC_CATEGORIES_KEY,
    get_doc_categories,
    validate_categories_payload,
)
from app.services.ingestion import (
    FORMAT_CATALOG,
    enabled_parse_exts,
    set_enabled_parse_exts,
)
from app.services.login_channels import (
    encrypt_wechat_secret,
    get_login_integrations,
    mask_login_integrations,
    save_login_integrations,
)

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"], dependencies=[Depends(require_admin)])


class FormatsUpdate(BaseModel):
    enabled: list[str] = Field(default_factory=list, description="启用的扩展名列表（小写含点，如 .pdf）")


@router.get("/formats")
async def list_formats(db: AsyncSession = Depends(get_db)):
    """解析文件格式清单（按类别分组 + 中文名 + 当前启用状态）。"""
    current = enabled_parse_exts()
    items = []
    for group in FORMAT_CATALOG:
        for ext in group["exts"]:
            items.append(
                {
                    "ext": ext,
                    "category": group["key"],
                    "category_label": group["label"],
                    "enabled": ext in current,
                }
            )
    return {"items": items}


@router.put("/formats")
async def update_formats(body: FormatsUpdate, db: AsyncSession = Depends(get_db)):
    """保存启用的解析格式，并立即刷新进程内缓存。"""
    enabled = await set_enabled_parse_exts(db, body.enabled)
    return {"ok": True, "enabled": sorted(enabled)}


# ---------------------------------------------------------------------------
# 登录与接入（短信验证码 / 微信配置框架），存 system_settings(key=login_integrations)
# ---------------------------------------------------------------------------


class SmsHttpConfig(BaseModel):
    """通用 HTTP 网关：POST url，body 模板支持 {phone} {code} 占位。"""

    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: str = '{"phone": "{phone}", "code": "{code}"}'


class SmsIntegrationConfig(BaseModel):
    enabled: bool = False
    provider: str = Field(default="log", pattern="^(log|http)$")  # aliyun/tencent 预留扩展位
    http: SmsHttpConfig = Field(default_factory=SmsHttpConfig)


class WechatIntegrationConfig(BaseModel):
    """微信登录预留：仅保存配置，登录流程后续接入。"""

    enabled: bool = False
    app_id: str = ""
    # None=不修改；""=清除；其余按明文加密存储
    app_secret: str | None = None
    redirect_uri: str = ""


class LoginIntegrationsUpdate(BaseModel):
    sms: SmsIntegrationConfig
    wechat: WechatIntegrationConfig


@router.get("/login-integrations")
async def get_login_integrations_api(db: AsyncSession = Depends(get_db)):
    """读取登录接入配置（wechat.app_secret 脱敏返回）。"""
    cfg = await get_login_integrations(db)
    return mask_login_integrations(cfg)


@router.put("/login-integrations")
async def update_login_integrations(
    body: LoginIntegrationsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存登录接入配置。app_secret 传 null 保持原值、传空串清除、其余按明文加密。"""
    current = await get_login_integrations(db)
    cfg = {
        "sms": body.sms.model_dump(),
        "wechat": {
            "enabled": body.wechat.enabled,
            "app_id": body.wechat.app_id.strip(),
            "app_secret": encrypt_wechat_secret(
                body.wechat.app_secret, current.get("wechat", {}).get("app_secret") or ""
            ),
            "redirect_uri": body.wechat.redirect_uri.strip(),
        },
    }
    await save_login_integrations(db, cfg)
    record_audit(
        db, admin, "update", "system_setting", None,
        {"key": "login_integrations", "sms_enabled": cfg["sms"]["enabled"],
         "wechat_enabled": cfg["wechat"]["enabled"]},
        request.client.host if request.client else None,
    )
    await db.commit()
    return mask_login_integrations(cfg)


# ---------------------------------------------------------------------------
# Email Guide（AI 邮件草稿写作规范），存 system_settings(key=email_guide)
# ---------------------------------------------------------------------------

EMAIL_GUIDE_KEY = "email_guide"


class EmailGuideUpdate(BaseModel):
    guide: str = ""


@router.get("/email-guide")
async def get_email_guide(db: AsyncSession = Depends(get_db)):
    """读取 Email Guide（无配置返回空串）。"""
    row = await db.get(SystemSetting, EMAIL_GUIDE_KEY)
    return {"guide": (row.value or "") if row else ""}


@router.put("/email-guide")
async def update_email_guide(
    body: EmailGuideUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存 Email Guide（AI 邮件草稿时注入 system prompt）。"""
    row = await db.get(SystemSetting, EMAIL_GUIDE_KEY)
    if row is None:
        row = SystemSetting(key=EMAIL_GUIDE_KEY)
        db.add(row)
    row.value = body.guide
    record_audit(
        db, admin, "update", "system_setting", None,
        {"key": EMAIL_GUIDE_KEY},
        request.client.host if request.client else None,
    )
    await db.commit()
    return {"guide": body.guide}


@router.get("/doc-categories")
async def get_doc_categories_admin(db: AsyncSession = Depends(get_db)):
    """读取资料类型配置（无配置返回默认五项）。"""
    return {"items": await get_doc_categories(db)}


@router.put("/doc-categories")
async def update_doc_categories(
    body: DocCategoriesUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """整体替换资料类型列表（校验：标识小写字母/数字/下划线且唯一，1~20 个）。

    删除类型不影响存量文件的 category 值（展示端对未知值回退显示原始标识）。
    """
    try:
        items = validate_categories_payload([it.model_dump() for it in body.items])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await db.get(SystemSetting, DOC_CATEGORIES_KEY)
    if row is None:
        row = SystemSetting(key=DOC_CATEGORIES_KEY)
        db.add(row)
    row.value = json.dumps(items, ensure_ascii=False)
    record_audit(
        db, admin, "update", "system_setting", None,
        {"key": DOC_CATEGORIES_KEY, "count": len(items)},
        request.client.host if request.client else None,
    )
    await db.commit()
    return {"items": items}
