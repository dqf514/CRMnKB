from datetime import datetime
from typing import Literal

from pydantic import BaseModel

TaskType = Literal["follow_up", "meeting", "call", "email", "report"]
TaskPriority = Literal["high", "medium", "low"]
TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]


class TaskCreate(BaseModel):
    customer_id: int | None = None
    title: str
    description: str | None = None
    type: TaskType = "follow_up"
    priority: TaskPriority = "medium"
    due_date: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    type: TaskType | None = None
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    status: TaskStatus | None = None


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
