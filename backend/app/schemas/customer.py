from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

CustomerStatus = Literal["potential", "intention", "negotiating", "closed", "lost"]


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


class CustomerOut(CustomerBase):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime


class CustomerListOut(BaseModel):
    items: list[CustomerOut]
    total: int


class CustomerProfileOut(BaseModel):
    profile: str | None = None
    status: str  # idle / generating / ready / failed
    updated_at: datetime | None = None
