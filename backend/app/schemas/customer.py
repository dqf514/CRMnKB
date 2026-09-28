from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CustomerStatus = Literal["potential", "intention", "negotiating", "closed", "lost"]

# DDQ（尽职调查）状态合法取值；update 端点手工校验（非法返回 400 而非 422）
DDQ_STATUSES = ("none", "pending", "completed")


class CustomerBase(BaseModel):
    name: str
    industries: list[str] = []
    tags: list[str] = []
    company: str | None = None
    position: str | None = None
    wechat: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    birthday: date | None = None
    attributes: dict = {}
    status: CustomerStatus = "potential"
    source: str | None = None


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: str | None = None
    industries: list[str] | None = None
    tags: list[str] | None = None
    company: str | None = None
    position: str | None = None
    wechat: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    birthday: date | None = None
    attributes: dict | None = None
    status: CustomerStatus | None = None
    source: str | None = None
    ddq_status: str | None = None


class CustomerOut(CustomerBase):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    owner_id: int | None = None
    ddq_status: str = "none"
    ai_brief: str | None = None
    ai_brief_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("ddq_status", mode="before")
    @classmethod
    def _default_ddq_status(cls, v):
        # 新建客户 flush 前 ORM 默认未落值（None），响应层兜底为 none
        return v or "none"


class CustomerListOut(BaseModel):
    items: list[CustomerOut]
    total: int


class CustomerProfileOut(BaseModel):
    profile: str | None = None
    status: str  # idle / generating / ready / failed
    updated_at: datetime | None = None


class EmailDraftRequest(BaseModel):
    """AI 邮件草稿请求：intent 必填（用户意图），language 支持 zh / en / zh_en。"""

    intent: str = Field(min_length=1, max_length=2000)
    language: str = "zh"


class EmailDraftOut(BaseModel):
    subject: str
    body: str
