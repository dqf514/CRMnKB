from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class OpportunityCreate(BaseModel):
    customer_id: int
    name: str
    amount: Decimal = Decimal("0")
    stage: str = "prospecting"
    expected_close_date: date | None = None
    probability: int = 0


class OpportunityUpdate(BaseModel):
    name: str | None = None
    amount: Decimal | None = None
    stage: str | None = None
    expected_close_date: date | None = None
    probability: int | None = None


class OpportunityOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    customer_id: int
    name: str
    amount: Decimal
    stage: str
    expected_close_date: date | None = None
    probability: int
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime
