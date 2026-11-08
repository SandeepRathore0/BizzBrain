from typing import Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    category_name: str = Field(..., min_length=1, max_length=100)


class CategoryUpdate(BaseModel):
    category_name: Optional[str] = Field(None, min_length=1, max_length=100)
    is_active: Optional[bool] = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    business_id: uuid.UUID
    category_name: str
    is_active: bool
