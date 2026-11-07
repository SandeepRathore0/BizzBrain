"""Business and Member Pydantic schemas."""

from typing import List, Optional
from pydantic import BaseModel, Field


class BusinessCreateRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=150)
    base_currency: str = Field(..., min_length=3, max_length=3)
    timezone: str = Field(..., min_length=1, max_length=50)


class BusinessUpdateRequest(BaseModel):
    business_name: Optional[str] = Field(None, min_length=1, max_length=150)
    timezone: Optional[str] = Field(None, min_length=1, max_length=50)
    # base_currency is immutable — not included


class BusinessOut(BaseModel):
    business_id: str
    business_name: str
    base_currency: str
    timezone: str
    is_active: bool
    created_at: str


class MemberOut(BaseModel):
    membership_id: str
    business_id: str
    user_id: str
    role: str
    is_active: bool
    created_at: str


class AddMemberRequest(BaseModel):
    user_id: str = Field(..., description="UUID of the user to add")
    role: str = Field(..., description="OWNER | ADMIN | EMPLOYEE")


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = Field(None, description="New role for the member")
    is_active: Optional[bool] = None
