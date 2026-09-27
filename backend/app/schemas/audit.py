from datetime import datetime

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int | None = None
    user_id: int | None = None
    action: str
    resource_type: str | None = None
    resource_id: int | None = None
    detail: dict | None = None
    ip: str | None = None
    created_at: datetime


class AuditLogListOut(BaseModel):
    items: list[AuditLogOut]
    total: int
