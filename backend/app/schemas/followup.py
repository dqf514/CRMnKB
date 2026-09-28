from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FollowUpCreate(BaseModel):
    type: Literal["call", "meeting", "email", "visit"]
    content: str
    # 下一步行动（可选）
    next_step: str | None = None


class FollowUpOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    customer_id: int
    user_id: int
    type: str
    content: str
    ai_summary: str | None = None
    next_step: str | None = None
    created_at: datetime
