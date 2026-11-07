"""Auth request / response Pydantic schemas."""

import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name must not be blank")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        if not _EMAIL_RE.match(v.strip()):
            raise ValueError("Invalid email address format")
        return v.strip()


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1)


class PasswordResetRequestIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        if not _EMAIL_RE.match(v.strip()):
            raise ValueError("Invalid email address format")
        return v.strip()


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds
    refresh_token: str


class UserOut(BaseModel):
    user_id: str
    full_name: str
    email: str
    is_active: bool
    email_verified_at: Optional[str] = None
    created_at: str
    updated_at: str


class UpdateMeRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=100)
