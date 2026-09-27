from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FeedbackCreate(BaseModel):
    query_log_id: int | None = None
    question: str | None = None
    answer: str | None = None
    rating: Literal["useful", "useless"]
    comment: str | None = None


class FeedbackOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    user_id: int
    query_log_id: int | None = None
    rating: str
    comment: str | None = None
    created_at: datetime


class FeedbackStatsOut(BaseModel):
    total: int
    useful: int
    useless: int
    useful_rate: float
