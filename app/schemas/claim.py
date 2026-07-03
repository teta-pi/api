from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class ClaimCreate(BaseModel):
    email: EmailStr
    entity_type: Literal["business", "journalist", "creator", "developer", "other"]
    ready_to_pay: bool = False
    source: dict | None = Field(default=None, description="UTM capture: utm_source, utm_medium, referrer")


class ClaimResponse(BaseModel):
    position: int


class ClaimStats(BaseModel):
    total: int
    pay_ready: int
    pay_ready_pct: float
