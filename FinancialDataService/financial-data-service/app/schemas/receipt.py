from datetime import datetime
import uuid
from pydantic import BaseModel, ConfigDict


class ReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_id: uuid.UUID
    expense_id: uuid.UUID
    business_id: uuid.UUID
    file_key: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: uuid.UUID
    uploaded_at: datetime


class ReceiptDownloadResponse(BaseModel):
    url: str
    expires_in_seconds: int = 900
