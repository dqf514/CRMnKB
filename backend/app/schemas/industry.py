from datetime import datetime

from pydantic import BaseModel


class IndustryCreate(BaseModel):
    name: str
    sort: int = 0
    enabled: bool = True


class IndustryUpdate(BaseModel):
    name: str | None = None
    sort: int | None = None
    enabled: bool | None = None


class IndustryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    sort: int
    enabled: bool
    created_at: datetime
