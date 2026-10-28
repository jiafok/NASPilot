"""User schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.auth import AccountRequest, UserOut


class UserCreate(AccountRequest):
    email: str | None = Field(None, max_length=255)
    is_admin: bool = False


class UserPage(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int


class UserUpdate(BaseModel):
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    preferences: dict | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


__all__ = ["UserOut", "UserCreate", "UserPage", "UserUpdate", "PasswordChange"]
