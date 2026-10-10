from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator

TaskType = Literal["follow_up", "meeting", "call", "email", "report", "todo"]
TaskPriority = Literal["high", "medium", "low"]
TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]


def _to_naive_utc(v: datetime | None) -> datetime | None:
    """带时区的输入统一转 UTC 后去掉时区信息（DB 列为 naive UTC TIMESTAMP）；naive 原样通过。"""
    if v is not None and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


class TaskCreate(BaseModel):
    customer_id: int | None = None
    title: str
    description: str | None = None
    type: TaskType = "follow_up"
    priority: TaskPriority = "medium"
    due_date: datetime | None = None

    _normalize_due_date = field_validator("due_date")(_to_naive_utc)


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    type: TaskType | None = None
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    status: TaskStatus | None = None

    _normalize_due_date = field_validator("due_date")(_to_naive_utc)


class TaskOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    customer_id: int | None = None
    customer_name: str | None = None
    user_id: int
    title: str
    description: str | None = None
    type: str
    priority: str
    due_date: datetime | None = None
    status: str
    ai_generated: bool
    source: str
    created_at: datetime
    completed_at: datetime | None = None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
