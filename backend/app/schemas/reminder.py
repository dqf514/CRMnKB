from datetime import datetime

from pydantic import BaseModel


class ReminderRuleCreate(BaseModel):
    name: str
    trigger_type: str  # days_since_last_followup / opportunity_stagnant / task_due_soon
    trigger_config: dict = {}
    action_config: dict = {}
    enabled: bool = True


class ReminderRuleUpdate(BaseModel):
    name: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    action_config: dict | None = None
    enabled: bool | None = None


class ReminderRuleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    name: str
    trigger_type: str
    trigger_config: dict
    action_config: dict
    enabled: bool
    created_at: datetime


class ReminderRunOut(BaseModel):
    tasks_created: int
    notifications_created: int


class NotificationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    user_id: int
    task_id: int | None = None
    title: str
    content: str | None = None
    type: str
    is_read: bool
    created_at: datetime
    # 分享通知的跳转目标（type='share'）
    resource_type: str | None = None
    resource_id: int | None = None


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int
