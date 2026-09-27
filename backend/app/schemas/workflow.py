from datetime import datetime

from pydantic import BaseModel


class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    trigger_type: str  # interval / daily / weekly / birthday / condition
    trigger_config: dict = {}
    conditions: list = []
    action_type: str  # send_email / create_task / create_notification
    action_config: dict = {}
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    conditions: list | None = None
    action_type: str | None = None
    action_config: dict | None = None
    enabled: bool | None = None


class WorkflowOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    name: str
    description: str | None = None
    trigger_type: str
    trigger_config: dict
    conditions: list
    action_type: str
    action_config: dict
    enabled: bool
    last_run_at: datetime | None = None
    created_at: datetime


class WorkflowRunOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    workflow_id: int
    status: str
    matched_count: int
    detail: str | None = None
    created_at: datetime


class WorkflowRunResult(BaseModel):
    run_id: int
    status: str
    matched_count: int
    detail: str | None = None


class WorkflowRunListOut(BaseModel):
    items: list[WorkflowRunOut]
    total: int
