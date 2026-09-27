from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FollowUpCreate(BaseModel):
    type: Literal["call", "meeting", "email", "visit"]
    content: str


class FollowUpOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    customer_id: int
    user_id: int
    type: str
    content: str
    ai_summary: str | None = None
    created_at: datetime
